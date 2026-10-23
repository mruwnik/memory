"""HTTP client for user-supplied servers that checks addresses at dial time.

`validate_public_url` resolves a host and the HTTP client then resolves it
again, so a DNS server answering differently the second time (rebinding)
gets past it. Here the network backend resolves once, checks every address,
and dials the address it checked. TLS SNI and certificate checks still use the
URL's hostname: httpcore passes the origin host to `start_tls`, not the
dialed address.

Private and LAN ranges are reachable (users register servers on their own
networks). Never reachable: loopback, link-local and cloud metadata,
unspecified/multicast/broadcast, the default docker/podman address pools,
this instance's own host (every port), and the stack's own services (compose
hostnames, and the container networks when MCP_BLOCK_CONTAINER_NETWORKS is set).
"""

import asyncio
import functools
import ipaddress
import logging
import re
import socket
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlparse

import httpcore
import httpx

from memory.common import settings
from memory.common.ssrf import UnsafeURLError

logger = logging.getLogger(__name__)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
Resolver = Callable[[str], Awaitable[list[IPAddress]]]

# Compose service names (docker-compose.yaml); each is also a DNS name on the
# stack's network.
STACK_SERVICES = (
    "postgres",
    "redis",
    "qdrant",
    "api",
    "worker",
    "agents-worker",
    "ingest-hub",
    "migrate",
    "backup",
)
ALWAYS_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "100.100.100.200/32",  # Alibaba Cloud metadata
        "fd00:ec2::254/128",  # AWS IMDS over IPv6
        # Default docker (172.17-31) and podman (10.88, 10.89) address pools.
        "172.16.0.0/12",
        "10.88.0.0/16",
        "10.89.0.0/16",
    )
)
NAT64 = ipaddress.ip_network("64:ff9b::/96")
IPV4_COMPATIBLE = ipaddress.ip_network("::/96")
STACK_TTL_SEC = 30.0
GENERIC_REFUSAL = "connection refused: destination not allowed"


class BlockedDestination(httpx.ConnectError, UnsafeURLError):
    """A transport error, so MCP and OAuth code treat it like any failed
    connection. Its message never names the resolved address."""

    def __init__(self, message: str = GENERIC_REFUSAL):
        httpx.ConnectError.__init__(self, message)


class ResponseTooLarge(httpx.ReadError):
    pass


class UnsupportedEncoding(httpx.DecodingError):
    pass


def embedded_ipv4(address: IPAddress) -> ipaddress.IPv4Address | None:
    if not isinstance(address, ipaddress.IPv6Address):
        return None
    if address.ipv4_mapped:
        return address.ipv4_mapped
    if address in NAT64 or address in IPV4_COMPATIBLE:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def always_blocked(address: IPAddress) -> bool:
    def bad(a: IPAddress) -> bool:
        return (
            a.is_loopback
            or a.is_link_local
            or a.is_unspecified
            or a.is_multicast
            or (isinstance(a, ipaddress.IPv4Address) and a.is_reserved)
            or any(a in net for net in ALWAYS_BLOCKED_NETWORKS)
        )

    inner = embedded_ipv4(address)
    return bad(address) or (inner is not None and bad(inner))


def normalize_host(host: str) -> str:
    return host.lower().rstrip(".")


@dataclass(frozen=True)
class NetworkPolicy:
    blocked_networks: tuple[IPNetwork, ...] = ()
    # Refused by name, and whatever they resolve to is refused on every port.
    blocked_hosts: frozenset[str] = frozenset()
    # Only test servers bind loopback; never set from configuration.
    loopback_allowed: bool = False
    # Refuse every address that isn't globally routable, the LAN included.
    public_only: bool = False

    def blocked(self, address: IPAddress, stack: frozenset[IPAddress]) -> bool:
        if self.loopback_allowed and address.is_loopback:
            return False
        inner = embedded_ipv4(address)
        candidates = [address] + ([inner] if inner else [])
        return always_blocked(address) or any(
            a in stack
            or any(a in net for net in self.blocked_networks)
            or (self.public_only and not a.is_global)
            for a in candidates
        )

    def check(
        self,
        host: str,
        addresses: list[IPAddress],
        stack: frozenset[IPAddress] = frozenset(),
    ) -> None:
        """Raise UnsafeURLError unless every address `host` resolved to may be
        dialed. `stack` is what the blocked hosts currently resolve to."""
        if normalize_host(host) in self.blocked_hosts:
            raise UnsafeURLError(f"{host} is one of this instance's own hosts")
        if not addresses:
            raise UnsafeURLError(f"could not resolve {host}")
        if bad := [a for a in addresses if self.blocked(a, stack)]:
            raise UnsafeURLError(f"{host} resolves to a blocked address {bad[0]}")


def parse_networks(value: str) -> tuple[IPNetwork, ...]:
    return tuple(
        ipaddress.ip_network(part.strip(), strict=False)
        for part in value.split(",")
        if part.strip()
    )


def connected_networks(route_table: str) -> list[IPNetwork]:
    """Directly connected IPv4 networks from a Linux /proc/net/route table."""

    def address(hex_le: str) -> ipaddress.IPv4Address:
        return ipaddress.IPv4Address(bytes.fromhex(hex_le)[::-1])

    networks: list[IPNetwork] = []
    for line in route_table.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 8:
            continue
        destination, gateway, mask = fields[1], fields[2], fields[7]
        if gateway != "00000000" or destination == "00000000":
            continue
        networks.append(ipaddress.ip_network(f"{address(destination)}/{address(mask)}"))
    return networks


def connected_networks_v6(route_table: str) -> list[IPNetwork]:
    """Directly connected IPv6 networks from /proc/net/ipv6_route (link-local,
    multicast and loopback are always blocked anyway)."""
    networks: list[IPNetwork] = []
    for line in route_table.splitlines():
        fields = line.split()
        if len(fields) < 10:
            continue
        destination, prefix, next_hop = fields[0], int(fields[1], 16), fields[4]
        if prefix == 0 or int(next_hop, 16) != 0:
            continue
        network = ipaddress.IPv6Network(
            (ipaddress.IPv6Address(int(destination, 16)), prefix)
        )
        if network.is_link_local or network.is_multicast or network.is_loopback:
            continue
        networks.append(network)
    return networks


def read_route_table(path: str, missing_ok: bool = False) -> str:
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        if missing_ok:
            return ""
        raise


def container_networks() -> list[IPNetwork]:
    """The container networks this process sits on, when the image says it runs
    in one. Raises rather than allowing everything if they can't be read."""
    if not settings.MCP_BLOCK_CONTAINER_NETWORKS:
        return []
    return connected_networks(read_route_table("/proc/net/route")) + connected_networks_v6(
        # Absent on kernels without IPv6, which then have no IPv6 networks.
        read_route_table("/proc/net/ipv6_route", missing_ok=True)
    )


@functools.cache
def default_policy() -> NetworkPolicy:
    """Static parts only; the stack's addresses are resolved per dial."""
    own_host = urlparse(settings.SERVER_URL).hostname or ""
    hosts = {
        normalize_host(h)
        for h in (
            *STACK_SERVICES,
            settings.DB_HOST,
            settings.REDIS_HOST,
            settings.QDRANT_HOST,
            own_host,
        )
        if h
    }
    return NetworkPolicy(
        blocked_networks=(
            *container_networks(),
            *parse_networks(settings.MCP_BLOCKED_NETWORKS),
        ),
        blocked_hosts=frozenset(hosts),
    )


async def resolve(host: str) -> list[IPAddress]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, None, type=socket.SOCK_STREAM
        )
    except (socket.gaierror, UnicodeError):
        return []
    return list(
        dict.fromkeys(
            ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]) for info in infos
        )
    )


class StackAddresses:
    """What the blocked hosts resolve to, re-resolved every STACK_TTL_SEC so a
    recreated container with a new address stays blocked."""

    def __init__(self, resolve: Resolver):
        self.resolve = resolve
        self.cached: dict[frozenset[str], tuple[float, frozenset[IPAddress]]] = {}

    async def get(self, hosts: frozenset[str]) -> frozenset[IPAddress]:
        hit = self.cached.get(hosts)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        answers = await asyncio.gather(*(self.resolve(h) for h in sorted(hosts)))
        addresses = frozenset(a for answer in answers for a in answer)
        self.cached[hosts] = (time.monotonic() + STACK_TTL_SEC, addresses)
        return addresses


class PinnedBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        policy: NetworkPolicy | None = None,
        resolve: Resolver = resolve,
        inner: httpcore.AsyncNetworkBackend | None = None,
    ):
        self.policy = policy
        self.resolve = resolve
        self.stack = StackAddresses(resolve)
        # httpcore declares a stub AnyIOBackend when anyio is absent, which hides its type.
        self.inner = inner or cast(httpcore.AsyncNetworkBackend, httpcore.AnyIOBackend())

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        policy = self.policy or default_policy()
        addresses = await self.resolve(host)
        try:
            policy.check(host, addresses, await self.stack.get(policy.blocked_hosts))
        except UnsafeURLError as e:
            logger.warning("refused to dial %s:%s: %s", host, port, e)
            raise BlockedDestination() from e
        last_error: Exception | None = None
        for address in addresses:
            try:
                return await self.inner.connect_tcp(
                    str(address),
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as e:
                last_error = e
        assert last_error is not None
        raise last_error

    async def connect_unix_socket(self, *args: Any, **kwargs: Any):
        raise BlockedDestination()

    async def sleep(self, seconds: float) -> None:
        await self.inner.sleep(seconds)


# The mapping httpx's own transport applies (httpx/_transports/default.py).
ERROR_MAP: dict[type[Exception], type[httpx.TransportError]] = {
    httpcore.TimeoutException: httpx.TimeoutException,
    httpcore.ConnectTimeout: httpx.ConnectTimeout,
    httpcore.ReadTimeout: httpx.ReadTimeout,
    httpcore.WriteTimeout: httpx.WriteTimeout,
    httpcore.PoolTimeout: httpx.PoolTimeout,
    httpcore.NetworkError: httpx.NetworkError,
    httpcore.ConnectError: httpx.ConnectError,
    httpcore.ReadError: httpx.ReadError,
    httpcore.WriteError: httpx.WriteError,
    httpcore.ProxyError: httpx.ProxyError,
    httpcore.UnsupportedProtocol: httpx.UnsupportedProtocol,
    httpcore.ProtocolError: httpx.ProtocolError,
    httpcore.LocalProtocolError: httpx.LocalProtocolError,
    httpcore.RemoteProtocolError: httpx.RemoteProtocolError,
}


class map_errors:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> None:
        if not isinstance(exc, Exception):
            return
        matches = [to for frm, to in ERROR_MAP.items() if isinstance(exc, frm)]
        if not matches:
            return
        most_specific = max(matches, key=lambda t: len(t.__mro__))
        raise most_specific(str(exc)) from exc


EVENT_BOUNDARY = re.compile(rb"\r\n\r\n|\n\n")


class CappedStream(httpx.AsyncByteStream):
    """Caps a response's bytes; with `per_event` on an event stream, each
    event's bytes, so a long-lived SSE connection isn't cut off for being
    long-lived."""

    def __init__(self, response: httpcore.Response, limit: int, per_event: bool):
        self.response = response
        self.limit = limit
        self.per_event = per_event

    async def __aiter__(self) -> AsyncIterator[bytes]:
        seen = 0
        with map_errors():
            async for chunk in cast(AsyncIterator[bytes], self.response.stream):
                first, *rest = (
                    EVENT_BOUNDARY.split(chunk) if self.per_event else [chunk]
                )
                # Each completed event is checked; `seen` carries the open one.
                for size in [seen + len(first), *map(len, rest)]:
                    if size > self.limit:
                        raise ResponseTooLarge(f"response exceeded {self.limit} bytes")
                seen = len(rest[-1]) if rest else seen + len(first)
                yield chunk

    async def aclose(self) -> None:
        await self.response.aclose()


class PinnedTransport(httpx.AsyncBaseTransport):
    def __init__(self, backend: PinnedBackend, max_response_bytes: int, per_event: bool):
        self.pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            network_backend=backend,
        )
        self.max_response_bytes = max_response_bytes
        # The caller's choice, never the response's: a server choosing its own
        # Content-Type mustn't lift the total cap on a buffered body.
        self.per_event = per_event

    async def __aenter__(self) -> "PinnedTransport":
        await self.pool.__aenter__()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        with map_errors():
            await self.pool.__aexit__(*exc_info)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # The byte cap counts what crosses the wire; a compressed body would be
        # inflated after it, past the cap.
        request.headers["Accept-Encoding"] = "identity"
        assert isinstance(request.stream, httpx.AsyncByteStream)
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        with map_errors():
            response = await self.pool.handle_async_request(core_request)
        headers = httpx.Headers(response.headers)
        if headers.get("content-encoding", "identity").lower() not in ("", "identity"):
            await response.aclose()
            raise UnsupportedEncoding("compressed responses are not accepted")
        per_event = self.per_event and headers.get("content-type", "").startswith(
            "text/event-stream"
        )
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=CappedStream(response, self.max_response_bytes, per_event),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self.pool.aclose()


ROUTING_KWARGS = ("transport", "mounts", "proxy", "proxies")


def public_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | float | None = None,
    auth: httpx.Auth | None = None,
    *,
    backend: PinnedBackend | None = None,
    max_response_bytes: int | None = None,
    per_event: bool = False,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """An httpx client for user-supplied URLs; also usable as fastmcp's
    `httpx_client_factory`. Redirects are never followed by the client (fastmcp
    asks for them), and proxies, mounts and proxy environment variables are
    refused or ignored: each would route around the dial-time check."""
    if routed := [k for k in ROUTING_KWARGS if k in kwargs]:
        raise ValueError(f"public_http_client does not accept {', '.join(routed)}")
    kwargs.pop("follow_redirects", None)
    transport = PinnedTransport(
        backend or PinnedBackend(),
        max_response_bytes or settings.MCP_MAX_RESPONSE_BYTES,
        per_event,
    )
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout or httpx.Timeout(30.0, read=300.0),
        auth=auth,
        transport=transport,
        trust_env=False,
        follow_redirects=False,
        **kwargs,
    )


def mcp_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | float | None = None,
    auth: httpx.Auth | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """fastmcp's `httpx_client_factory`: MCP streams are capped per event."""
    return public_http_client(headers, timeout, auth, per_event=True, **kwargs)
