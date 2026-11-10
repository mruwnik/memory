"""Mail servers are checked at dial time, and dialed at the address checked."""

import socket

import pytest

from memory.common import mail_connect
from memory.common.db.models import EmailAccount
from memory.common.email_sender import SmtpConfig, send_via_smtp
from memory.workers.email import imap_connection

HOST = "mail.example.com"
PUBLIC = "93.184.216.34"
BLOCKED = ["127.0.0.1", "10.1.2.3", "192.168.0.7", "169.254.169.254", "::1"]


class Network:
    """Fake DNS answering `answers` for every host, and a dialer that records
    each (address, port) dialed and then fails."""

    def __init__(self, answers: list[str]):
        self.answers = answers
        self.lookups = 0
        self.dialed: list[tuple[str, int]] = []

    def getaddrinfo(self, host, port, *args, **kwargs):
        self.lookups += 1
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (a, 0, 0, 0))
            if ":" in a
            else (socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0))
            for a in self.answers
        ]

    def create_connection(self, address, *args, **kwargs):
        self.dialed.append(address)
        raise ConnectionRefusedError("test network dials nowhere")


@pytest.fixture
def network(monkeypatch):
    def install(answers: list[str]) -> Network:
        net = Network(answers)
        monkeypatch.setattr(socket, "getaddrinfo", net.getaddrinfo)
        monkeypatch.setattr(socket, "create_connection", net.create_connection)
        return net

    return install


def imap_account(port: int, use_ssl: bool) -> EmailAccount:
    return EmailAccount(
        imap_server=HOST, imap_port=port, username="u", password="p", use_ssl=use_ssl
    )


def smtp_config(port: int) -> SmtpConfig:
    return SmtpConfig(
        email_address="me@example.com",
        username=None,
        password="pw",
        smtp_server=HOST,
        smtp_port=port,
        imap_server=None,
    )


@pytest.mark.parametrize("address", BLOCKED)
@pytest.mark.parametrize("port, use_ssl", [(993, True), (143, False)])
def test_imap_connection_refuses_host_resolving_to_internal_address(
    network, address, port, use_ssl
):
    net = network([address])

    with pytest.raises(mail_connect.BlockedMailHost):
        with imap_connection(imap_account(port, use_ssl)):
            pytest.fail("connection must not be yielded")

    assert net.dialed == []


@pytest.mark.parametrize("address", BLOCKED)
@pytest.mark.parametrize("port", [465, 587])
def test_smtp_send_refuses_host_resolving_to_internal_address(network, address, port):
    net = network([address])

    result = send_via_smtp(smtp_config(port), to=["to@example.com"], subject="s", body="b")

    assert result.success is False
    assert net.dialed == []


def test_mixed_answer_is_refused(network):
    net = network([PUBLIC, "10.0.0.1"])

    with pytest.raises(mail_connect.BlockedMailHost):
        mail_connect.connect_public(HOST, 993, 5)

    assert net.dialed == []


@pytest.mark.parametrize("port, use_ssl", [(993, True), (143, False)])
def test_imap_dials_the_checked_address_not_the_hostname(network, port, use_ssl):
    net = network([PUBLIC])

    with pytest.raises(ConnectionRefusedError):
        with imap_connection(imap_account(port, use_ssl)):
            pytest.fail("connection must not be yielded")

    assert net.dialed == [(PUBLIC, port)]
    assert net.lookups == 1


@pytest.mark.parametrize("port", [465, 587])
def test_smtp_dials_the_checked_address_not_the_hostname(network, port):
    net = network([PUBLIC])

    result = send_via_smtp(smtp_config(port), to=["to@example.com"], subject="s", body="b")

    assert result.success is False
    assert net.dialed == [(PUBLIC, port)]
    assert net.lookups == 1


def test_blocked_refusal_never_names_the_address(network):
    network(["10.9.8.7"])

    with pytest.raises(mail_connect.BlockedMailHost) as raised:
        mail_connect.connect_public(HOST, 993, 5)

    assert "10.9.8.7" not in str(raised.value)


class StopHandshake(Exception):
    pass


class RecordingContext:
    def __init__(self):
        self.server_hostnames: list[str | None] = []

    def wrap_socket(self, sock, server_hostname=None, **kwargs):
        self.server_hostnames.append(server_hostname)
        sock.close()
        raise StopHandshake()


@pytest.mark.parametrize(
    "connect",
    [
        lambda ctx: mail_connect.PublicIMAP4SSL(HOST, 993, ssl_context=ctx),
        lambda ctx: mail_connect.PublicSMTPSSL(HOST, 465, context=ctx),
    ],
)
def test_implicit_tls_verifies_the_hostname_not_the_dialed_address(
    monkeypatch, connect
):
    net = Network([PUBLIC])
    peers: list[socket.socket] = []

    def dial(address, *args, **kwargs):
        net.dialed.append(address)
        ours, theirs = socket.socketpair()
        peers.append(theirs)
        return ours

    monkeypatch.setattr(socket, "getaddrinfo", net.getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", dial)
    context = RecordingContext()

    with pytest.raises(StopHandshake):
        connect(context)

    for peer in peers:
        peer.close()
    assert net.dialed[0][0] == PUBLIC
    assert context.server_hostnames == [HOST]
