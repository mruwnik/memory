"""A session for caldav's DAVClient whose every request is checked at dial time.

A CalDAV server names the URLs the client fetches next (principal, calendar
home, calendars, events) and may redirect, so checking the account URL once
is not enough. This session sends each request through safe_http's pinned,
public-only client: it resolves the host, refuses non-public addresses, dials
the checked address, ignores proxy and netrc environment, and caps the
response size. GET redirects are followed by httpx through the same transport,
dropping credentials when the origin changes.
"""

from typing import Any, cast

import httpx
from caldav.davclient import DAVClient
from niquests.auth import HTTPDigestAuth

from memory.common.safe_http import NetworkPolicy, public_sync_http_client

CALDAV_TIMEOUT_SEC = 30.0
CALDAV_MAX_RESPONSE_BYTES = 50 * 1024 * 1024


def httpx_auth(auth: Any) -> Any:
    """httpx auth for one of caldav's niquests auth objects. Basic and Bearer
    only set a header, so httpx can call them on its own request; Digest
    needs httpx's challenge-response flow."""
    if isinstance(auth, HTTPDigestAuth):
        return httpx.DigestAuth(auth.username, auth.password)
    return auth


class PinnedDAVSession:
    """Stands in for the niquests session DAVClient makes for itself."""

    def __init__(self, policy: NetworkPolicy | None = None):
        self.client = public_sync_http_client(
            timeout=CALDAV_TIMEOUT_SEC,
            max_response_bytes=CALDAV_MAX_RESPONSE_BYTES,
            policy=policy,
        )
        # DAVClient passes the same auth object on every request. Converting
        # it once keeps httpx.DigestAuth's nonce, so each request after the
        # first answers the challenge up front instead of taking a 401 first.
        self.auth_source: Any = None
        self.auth: Any = None

    def converted_auth(self, auth: Any) -> Any:
        if auth is not self.auth_source:
            self.auth_source = auth
            self.auth = httpx_auth(auth)
        return self.auth

    def request(
        self,
        method: str,
        url: str,
        *,
        data: bytes | str | None = None,
        headers: Any = None,
        auth: Any = None,
        allow_redirects: bool = False,
        **_ignored: Any,
    ) -> httpx.Response:
        """caldav's `proxies`, `verify`, `cert` and `timeout` are ignored: no
        proxy is used, TLS is always verified and the client's timeout applies."""
        return self.client.request(
            method,
            url,
            content=data,
            headers=headers,
            auth=self.converted_auth(auth),
            follow_redirects=allow_redirects,
        )

    def close(self) -> None:
        self.client.close()


def pin_session(client: DAVClient, policy: NetworkPolicy | None = None) -> DAVClient:
    """Replace `client`'s own niquests session with a PinnedDAVSession."""
    client.session.close()
    cast(Any, client).session = PinnedDAVSession(policy)
    return client
