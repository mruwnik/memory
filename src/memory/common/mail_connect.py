"""IMAP and SMTP clients that check the mail server's address at dial time.

An account's server is checked when it is saved, but DNS can point it
elsewhere afterwards. These clients resolve the host once when connecting,
refuse it unless every address is public (the same rule as saving), and dial
an address they checked. TLS SNI and certificate checks still use the
hostname: imaplib and smtplib wrap the socket with ``server_hostname`` set to
the host they were given, for implicit TLS and for STARTTLS alike.
"""

import imaplib
import logging
import smtplib
import socket

from memory.common.safe_http import GENERIC_REFUSAL, IPAddress, NetworkPolicy
from memory.common.ssrf import UnsafeURLError, resolve_hostname

logger = logging.getLogger(__name__)

MAIL_POLICY = NetworkPolicy(public_only=True)


class BlockedMailHost(ConnectionRefusedError):
    """A failed connection to callers. Its message never names the address."""

    def __init__(self, message: str = GENERIC_REFUSAL):
        super().__init__(message)


def public_addresses(host: str) -> list[IPAddress]:
    """Every address `host` resolves to; UnsafeURLError unless all are public."""
    addresses = list(dict.fromkeys(resolve_hostname(host)))
    MAIL_POLICY.check(host, addresses)
    return addresses


def connect_public(host: str, port: int, timeout: float | None) -> socket.socket:
    """A TCP connection to one of `host`'s checked addresses."""
    try:
        addresses = public_addresses(host)
    except UnsafeURLError as e:
        logger.warning("refused to dial mail server %s:%s: %s", host, port, e)
        raise BlockedMailHost() from e
    last_error: OSError | None = None
    for address in addresses:
        try:
            return socket.create_connection((str(address), port), timeout)
        except OSError as e:
            last_error = e
    assert last_error is not None
    raise last_error


def socket_timeout(timeout: float | None) -> float | None:
    """imaplib passes None for "the default timeout"."""
    return socket.getdefaulttimeout() if timeout is None else timeout


class PublicIMAP4(imaplib.IMAP4):
    def _create_socket(self, timeout: float | None = None) -> socket.socket:
        return connect_public(self.host, self.port, socket_timeout(timeout))


class PublicIMAP4SSL(imaplib.IMAP4_SSL):
    def _create_socket(self, timeout: float | None = None) -> socket.socket:
        sock = connect_public(self.host, self.port, socket_timeout(timeout))
        return self.ssl_context.wrap_socket(sock, server_hostname=self.host)


class PublicSMTP(smtplib.SMTP):
    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        return connect_public(host, port, timeout)


class PublicSMTPSSL(smtplib.SMTP_SSL):
    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        sock = connect_public(host, port, timeout)
        return self.context.wrap_socket(sock, server_hostname=host)
