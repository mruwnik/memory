import asyncio
import gzip
import ipaddress

import httpcore
import httpx
import pytest

from memory.common import safe_http, settings
from memory.common.safe_http import (
    BlockedDestination,
    NetworkPolicy,
    PinnedBackend,
    PinnedTransport,
    ResponseTooLarge,
    UnsupportedEncoding,
    connected_networks,
    connected_networks_v6,
    default_policy,
    public_http_client,
)
from memory.common.ssrf import UnsafeURLError

ip = ipaddress.ip_address


class resolver_for:
    """Answers from `table`; a list of lists answers differently on each lookup."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    async def __call__(self, host):
        self.calls.append(host)
        answer = self.table.get(host, [])
        current = answer.pop(0) if answer and isinstance(answer[0], list) else answer
        return [ip(a) for a in current]


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.8.8.8",
        "::1",
        "::ffff:127.0.0.1",
        "64:ff9b::7f00:1",  # NAT64-embedded loopback
        "::a9fe:a9fe",  # IPv4-compatible 169.254.169.254
        "169.254.169.254",
        "169.254.0.1",
        "fe80::1",
        "fd00:ec2::254",
        "100.100.100.200",
        "0.0.0.0",
        "0.1.2.3",
        "::",
        "224.0.0.1",
        "ff02::1",
        "255.255.255.255",
        "172.17.0.2",  # docker default bridge
        "172.20.1.5",
        "10.88.0.4",  # podman default
        "10.89.0.4",
    ],
)
def test_always_blocked_addresses_are_refused(address):
    with pytest.raises(UnsafeURLError):
        NetworkPolicy().check("mcp.example.com", [ip(address)])


@pytest.mark.parametrize(
    "address",
    ["8.8.8.8", "10.0.0.5", "192.168.1.20", "100.64.0.1", "fd00::1", "64:ff9b::808:808"],
)
def test_public_and_private_addresses_are_allowed(address):
    NetworkPolicy().check("mcp.example.com", [ip(address)])


def test_any_blocked_address_refuses_the_host():
    with pytest.raises(UnsafeURLError):
        NetworkPolicy().check("mcp.example.com", [ip("8.8.8.8"), ip("127.0.0.1")])


def test_an_unresolvable_host_is_refused():
    with pytest.raises(UnsafeURLError):
        NetworkPolicy().check("mcp.example.com", [])


STACK = NetworkPolicy(
    blocked_networks=(ipaddress.ip_network("10.0.0.0/24"),),
    blocked_hosts=frozenset({"postgres", "memory.example.com"}),
)


@pytest.mark.parametrize(
    "host,address",
    [
        ("10.0.0.7", "10.0.0.7"),  # inside a blocked network
        ("postgres", "192.168.5.5"),  # a stack host by name
        ("POSTGRES.", "192.168.5.5"),
        ("memory.example.com", "8.8.4.4"),  # own host by name
        ("alias.example.com", "1.2.3.4"),  # resolves to a stack host's address
        ("alias.example.com", "::ffff:1.2.3.4"),
    ],
)
def test_the_stacks_own_hosts_are_refused_on_every_port(host, address):
    with pytest.raises(UnsafeURLError):
        STACK.check(host, [ip(address)], stack=frozenset({ip("1.2.3.4")}))


ROUTES = """\
Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0
eth0\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0
eth1\t0010A8C0\t00000000\t0001\t0\t0\t0\t00F0FFFF\t0\t0\t0
"""

ROUTES_V6 = """\
fd4d6169706f64000000000000000000 40 00000000000000000000000000000000 00 00000000000000000000000000000000 00000100 00000001 00000000 00000001 eth0
fe800000000000000000000000000000 40 00000000000000000000000000000000 00 00000000000000000000000000000000 00000100 00000001 00000000 00000001 eth0
00000000000000000000000000000000 00 00000000000000000000000000000000 00 fd4d6169706f64000000000000000001 00000400 00000001 00000000 00000003 eth0
ff000000000000000000000000000000 08 00000000000000000000000000000000 00 00000000000000000000000000000000 00000100 00000004 00000000 00000001 eth0
"""


def test_connected_networks_are_read_from_the_route_tables():
    assert connected_networks(ROUTES) == [
        ipaddress.ip_network("172.18.0.0/16"),
        ipaddress.ip_network("192.168.16.0/20"),
    ]
    assert connected_networks_v6(ROUTES_V6) == [
        ipaddress.ip_network("fd4d:6169:706f:6400::/64")
    ]


@pytest.fixture
def fresh_policy():
    default_policy.cache_clear()
    yield default_policy
    default_policy.cache_clear()


def test_default_policy_blocks_the_stack(monkeypatch, fresh_policy):
    monkeypatch.setattr(settings, "SERVER_URL", "https://Memory.Example.com:8443")
    monkeypatch.setattr(settings, "MCP_BLOCKED_NETWORKS", "10.1.0.0/16, 10.2.0.0/16")

    policy = fresh_policy()

    assert {"postgres", "redis", "qdrant", "api", "memory.example.com"} <= policy.blocked_hosts
    assert ipaddress.ip_network("10.2.0.0/16") in policy.blocked_networks


def test_container_networks_fail_closed(monkeypatch, fresh_policy):
    monkeypatch.setattr(settings, "MCP_BLOCK_CONTAINER_NETWORKS", True)

    def unreadable(path, missing_ok=False):
        raise PermissionError(path)

    monkeypatch.setattr(safe_http, "read_route_table", unreadable)

    with pytest.raises(PermissionError):
        fresh_policy()


def test_container_networks_are_blocked_when_enabled(monkeypatch, fresh_policy):
    monkeypatch.setattr(settings, "MCP_BLOCK_CONTAINER_NETWORKS", True)
    tables = {"/proc/net/route": ROUTES, "/proc/net/ipv6_route": ROUTES_V6}
    monkeypatch.setattr(
        safe_http, "read_route_table", lambda path, missing_ok=False: tables[path]
    )

    with pytest.raises(UnsafeURLError):
        fresh_policy().check("x.example.com", [ip("192.168.17.3")])


class RecordingStream(httpcore.AsyncNetworkStream):
    def __init__(self, log):
        self.log = log

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.log.append(("tls", server_hostname))
        raise RuntimeError("stop after tls")

    async def write(self, buffer, timeout=None):
        self.log.append(("write", buffer))
        raise RuntimeError("stop after write")

    async def read(self, max_bytes, timeout=None):
        return b""

    async def aclose(self):
        pass


class RecordingBackend(httpcore.AsyncNetworkBackend):
    def __init__(self):
        self.log = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.log.append(("dial", host, port))
        return RecordingStream(self.log)

    async def sleep(self, seconds):
        pass


async def fetch_through(url, resolve, policy=NetworkPolicy()):
    inner = RecordingBackend()
    async with public_http_client(
        backend=PinnedBackend(policy=policy, resolve=resolve, inner=inner)
    ) as client:
        with pytest.raises(RuntimeError, match="stop after"):
            await client.get(url)
    return inner.log


@pytest.mark.asyncio
async def test_the_validated_address_is_the_dialed_address():
    resolve = resolver_for({"mcp.example.com": [["8.8.8.8"], ["127.0.0.1"]]})

    log = await fetch_through("https://mcp.example.com/mcp", resolve)

    assert log[0] == ("dial", "8.8.8.8", 443)
    assert resolve.calls == ["mcp.example.com"]


@pytest.mark.asyncio
async def test_tls_and_host_header_keep_the_hostname():
    resolve = resolver_for({"mcp.example.com": ["8.8.8.8"]})

    tls = await fetch_through("https://mcp.example.com/mcp", resolve)
    plain = await fetch_through("http://mcp.example.com/mcp", resolve)

    assert tls[1] == ("tls", "mcp.example.com")
    assert b"Host: mcp.example.com\r\n" in plain[1][1]
    assert b"Accept-Encoding: identity\r\n" in plain[1][1]


@pytest.mark.asyncio
async def test_a_blocked_destination_is_a_generic_connect_error_and_never_dialed():
    inner = RecordingBackend()
    resolve = resolver_for({"rebind.example.com": ["169.254.169.254"]})
    async with public_http_client(
        backend=PinnedBackend(policy=NetworkPolicy(), resolve=resolve, inner=inner)
    ) as client:
        with pytest.raises(httpx.ConnectError) as raised:
            await client.get("http://rebind.example.com/latest/meta-data/")

    assert isinstance(raised.value, BlockedDestination)
    assert "169.254" not in str(raised.value)
    assert inner.log == []


@pytest.mark.asyncio
async def test_a_host_aliasing_a_stack_service_is_refused():
    inner = RecordingBackend()
    resolve = resolver_for({"alias.example.com": ["192.168.9.9"], "postgres": ["192.168.9.9"]})
    policy = NetworkPolicy(blocked_hosts=frozenset({"postgres"}))
    async with public_http_client(
        backend=PinnedBackend(policy=policy, resolve=resolve, inner=inner)
    ) as client:
        with pytest.raises(BlockedDestination):
            await client.get("http://alias.example.com:9999/")

    assert inner.log == []


@pytest.mark.asyncio
async def test_proxy_environment_is_ignored(monkeypatch):
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(var, "http://proxy.example.com:3128")
    resolve = resolver_for({"mcp.example.com": ["8.8.8.8"]})

    log = await fetch_through("http://mcp.example.com/mcp", resolve)

    assert log[0] == ("dial", "8.8.8.8", 80)


@pytest.mark.parametrize(
    "kwarg",
    [
        {"proxy": "http://proxy.example.com:3128"},
        {"mounts": {}},
        {"transport": httpx.AsyncHTTPTransport()},
        {"proxies": {}},
    ],
)
def test_routing_overrides_are_refused(kwarg):
    with pytest.raises(ValueError):
        public_http_client(**kwarg)


def test_the_client_uses_the_pinned_transport():
    client = public_http_client()

    assert isinstance(client._transport, PinnedTransport)  # type: ignore[attr-defined]


async def serve_once(response: bytes):
    async def handle(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(response)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


LOOPBACK_OK = NetworkPolicy(loopback_allowed=True)


async def get_local(raw: bytes, max_response_bytes=None, **client_kwargs) -> httpx.Response:
    server, port = await serve_once(raw)
    resolve = resolver_for({"mcp.example.com": ["127.0.0.1"]})
    async with server, public_http_client(
        backend=PinnedBackend(policy=LOOPBACK_OK, resolve=resolve),
        max_response_bytes=max_response_bytes,
        **client_kwargs,
    ) as client:
        return await client.get(f"http://mcp.example.com:{port}/mcp")


@pytest.mark.asyncio
async def test_redirects_are_not_followed():
    response = await get_local(
        b"HTTP/1.1 302 Found\r\nLocation: http://169.254.169.254/\r\nContent-Length: 0\r\n\r\n",
        follow_redirects=True,
    )

    assert response.status_code == 302


@pytest.mark.asyncio
async def test_oversized_responses_are_aborted():
    with pytest.raises(ResponseTooLarge):
        await get_local(
            b"HTTP/1.1 200 OK\r\nContent-Length: 5000\r\n\r\n" + b"x" * 5000,
            max_response_bytes=1000,
        )


@pytest.mark.asyncio
async def test_compressed_responses_are_refused():
    bomb = gzip.compress(b"\0" * 10_000_000)
    headers = f"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: {len(bomb)}\r\n\r\n"

    with pytest.raises(UnsupportedEncoding):
        await get_local(headers.encode() + bomb, max_response_bytes=1_000_000)


EVENTS = b"data: " + b"x" * 600 + b"\n\n"
EVENT_STREAM = (
    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: %d\r\n\r\n"
    % (len(EVENTS) * 5)
) + EVENTS * 5


@pytest.mark.asyncio
async def test_mcp_streams_are_capped_per_event():
    response = await get_local(EVENT_STREAM, max_response_bytes=1000, per_event=True)

    assert response.text.count("data: ") == 5


@pytest.mark.asyncio
async def test_a_response_cannot_lift_the_total_cap_by_calling_itself_a_stream():
    """OAuth calls buffer the body; the server's Content-Type mustn't matter."""
    with pytest.raises(ResponseTooLarge):
        await get_local(EVENT_STREAM, max_response_bytes=1000)


@pytest.mark.asyncio
async def test_one_oversized_event_is_aborted():
    event = b"data: " + b"x" * 2000 + b"\n\n"
    stream = (
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: %d\r\n\r\n"
        % len(event)
    )

    with pytest.raises(ResponseTooLarge):
        await get_local(stream + event, max_response_bytes=1000, per_event=True)


def test_sync_backend_pins_checked_address_across_dns_rebinding(monkeypatch):
    from unittest.mock import Mock
    import socket
    answers = iter(["93.184.216.34", "127.0.0.1"])
    calls = []

    def resolve(host, port, **kwargs):
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    inner = Mock()
    backend = safe_http.PinnedSyncBackend(inner=inner)
    backend.connect_tcp("rebind.example", 443)
    assert calls == ["rebind.example"]
    assert inner.connect_tcp.call_args.args == ("93.184.216.34", 443)
    with pytest.raises(BlockedDestination):
        backend.connect_tcp("rebind.example", 443)
    assert inner.connect_tcp.call_count == 1


@pytest.mark.parametrize("address", ["10.0.0.1", "127.0.0.1", "169.254.169.254", "::1", "192.168.1.1"])
def test_sync_backend_denies_nonpublic_addresses(monkeypatch, address):
    from unittest.mock import Mock
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", (address, 80))])
    inner = Mock()
    with pytest.raises(BlockedDestination):
        safe_http.PinnedSyncBackend(inner=inner).connect_tcp("untrusted.example", 80)
    inner.connect_tcp.assert_not_called()


def test_sync_transport_enforces_stream_cap_and_preserves_tls_hostname():
    from unittest.mock import Mock
    class Stream(httpcore.NetworkStream):
        def __init__(self):
            self.raw = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello"
            self.server_hostname = None
        def read(self, max_bytes, timeout=None):
            result, self.raw = self.raw[:max_bytes], self.raw[max_bytes:]
            return result
        def write(self, buffer, timeout=None):
            pass
        def close(self):
            pass
        def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            self.server_hostname = server_hostname
            return self
        def get_extra_info(self, info):
            return None
    stream = Stream()
    backend = Mock()
    backend.connect_tcp.return_value = stream
    with httpx.Client(transport=safe_http.PinnedSyncTransport(4, backend)) as client:
        with pytest.raises(ResponseTooLarge):
            client.get("https://original.example/file")
    assert stream.server_hostname == "original.example"
