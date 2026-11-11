import ipaddress
import socket
import threading
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from caldav.davclient import DAVClient

from memory.common.safe_http import BlockedDestination, NetworkPolicy
from memory.workers.caldav_session import pin_session
from memory.workers.tasks.calendar import CalDAVSyncError, fetch_caldav_events

Answer = tuple[int, dict[str, str], bytes]
Handler = Callable[[BaseHTTPRequestHandler], Answer]
ALLOWED = ipaddress.ip_address("127.0.0.1")
# Loopback, but not ALLOWED. 127.0.0.2 would do on Linux, but macOS only
# configures 127.0.0.1.
INTERNAL = "::1"


class OnlyFirstLoopback(NetworkPolicy):
    """The default policy, except that the test's DAV server on 127.0.0.1 may be
    dialed. Every other loopback address (the "internal" server) stays blocked."""

    def blocked(self, address, stack):
        return address != ALLOWED and super().blocked(address, stack)


class IPv6HTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


class Server:
    def __init__(self, host: str, answer: Handler):
        self.hits: list[tuple[str, str, str | None]] = []
        hits = self.hits

        class H(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def handle_any(self):
                self.rfile.read(int(self.headers.get("content-length") or 0))
                hits.append(
                    (self.command, self.path, self.headers.get("authorization"))
                )
                status, headers, body = answer(self)
                self.send_response(status)
                for name, value in {
                    "Content-Type": "application/xml",
                    **headers,
                }.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_PROPFIND = do_REPORT = do_OPTIONS = handle_any

        address = ipaddress.ip_address(host)
        server_class = ThreadingHTTPServer if address.version == 4 else IPv6HTTPServer
        self.httpd = server_class((host, 0), H)
        netloc = host if address.version == 4 else f"[{host}]"
        self.url = f"http://{netloc}:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def servers() -> Iterator[Callable[[str, Handler], Server]]:
    started: list[Server] = []

    def start(host: str, answer: Handler) -> Server:
        server = Server(host, answer)
        started.append(server)
        return server

    yield start
    for server in started:
        server.close()


def multistatus(path: str, prop: str) -> bytes:
    return (
        '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" '
        'xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response>'
        f"<d:href>{path}</d:href><d:propstat><d:prop>{prop}</d:prop>"
        "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
        "</d:multistatus>"
    ).encode()


def principal_answer(h: BaseHTTPRequestHandler) -> Answer:
    prop = "<d:current-user-principal><d:href>/p/</d:href></d:current-user-principal>"
    return 207, {}, multistatus(h.path, prop)


def home_set_answer(home: str) -> Handler:
    def answer(h: BaseHTTPRequestHandler) -> Answer:
        if h.path == "/":
            return principal_answer(h)
        prop = f"<c:calendar-home-set><d:href>{home}</d:href></c:calendar-home-set>"
        return 207, {}, multistatus(h.path, prop)

    return answer


def secret_answer(h: BaseHTTPRequestHandler) -> Answer:
    return 200, {}, b"internal secret"


def test_fetch_never_dials_a_non_public_server(servers):
    dav = servers("127.0.0.1", principal_answer)
    now = datetime.now(timezone.utc)

    with pytest.raises(CalDAVSyncError, match="^Could not reach the CalDAV server$"):
        fetch_caldav_events(dav.url + "/", "user", "hunter2", [], now, now)

    assert dav.hits == []


def test_a_calendar_home_on_another_host_is_never_dialed(servers):
    internal = servers(INTERNAL, secret_answer)
    dav = servers("127.0.0.1", home_set_answer(internal.url + "/latest/meta-data/"))
    client = pin_session(
        DAVClient(url=dav.url + "/", username="u", password="p"), OnlyFirstLoopback()
    )

    with pytest.raises(BlockedDestination):
        client.principal().calendars()

    assert internal.hits == []
    assert len(dav.hits) >= 2  # the principal and its calendar-home-set


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirect_to_another_host_is_never_followed(servers, status):
    internal = servers(INTERNAL, secret_answer)
    dav = servers(
        "127.0.0.1", lambda h: (status, {"Location": internal.url + "/x"}, b"")
    )
    client = pin_session(
        DAVClient(url=dav.url + "/", username="u", password="p"), OnlyFirstLoopback()
    )

    with pytest.raises(BlockedDestination):
        client.request(dav.url + "/event.ics", "GET")

    assert internal.hits == []


def test_a_same_host_redirect_is_followed(servers):
    def answer(h: BaseHTTPRequestHandler) -> Answer:
        if h.path == "/old.ics":
            return 302, {"Location": "/new.ics"}, b""
        return 200, {"Content-Type": "text/calendar"}, b"BEGIN:VCALENDAR"

    dav = servers("127.0.0.1", answer)
    client = pin_session(DAVClient(url=dav.url + "/"), OnlyFirstLoopback())

    response = client.request(dav.url + "/old.ics", "GET")

    assert response.status == 200
    assert [path for _, path, _ in dav.hits] == ["/old.ics", "/new.ics"]


@pytest.mark.parametrize(
    "challenge, scheme",
    [
        ('Basic realm="cal"', "Basic "),
        ('Digest realm="cal", nonce="abc", qop="auth"', "Digest "),
    ],
)
def test_server_auth_challenges_are_answered(servers, challenge, scheme):
    def answer(h: BaseHTTPRequestHandler) -> Answer:
        if not (h.headers.get("authorization") or "").startswith(scheme):
            return 401, {"WWW-Authenticate": challenge}, b""
        return principal_answer(h)

    dav = servers("127.0.0.1", answer)
    client = pin_session(
        DAVClient(url=dav.url + "/", username="u", password="p"), OnlyFirstLoopback()
    )

    client.principal()

    assert (dav.hits[-1][2] or "").startswith(scheme)
