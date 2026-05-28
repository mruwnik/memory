"""Startup safety checks for the FastAPI app.

Lives outside settings.py so settings.py can stay a leaf in the import
graph (env reads + lightweight helpers). The validators here consult
settings at runtime; settings does not depend on them.
"""

import logging
from urllib.parse import urlparse

from memory.common import settings

logger = logging.getLogger(__name__)


def is_loopback_url(url: str) -> bool:
    """Return True if ``url`` clearly points at the local machine.

    Only the exact loopback hostnames are accepted; anything else
    (including IPv6 link-local, ``.local`` mDNS, private RFC1918, etc.)
    is treated as non-loopback so the safety check fails closed.

    ``0.0.0.0`` is intentionally **not** in the loopback set. It is the
    wildcard bind address (``INADDR_ANY``) — semantically "listen on
    every interface" — so a ``SERVER_URL=http://0.0.0.0:8000`` is a
    statement of intent to reach the API from elsewhere on the network,
    not a loopback declaration. The platform's resolution of dialing
    ``0.0.0.0`` is also OS-dependent (loopback on Linux, the public IP
    on Windows / some routed setups) so silently treating it as loopback
    would be a false-negative for the safety check.
    """
    if not url:
        return True
    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def validate_disable_auth_safety() -> None:
    """Refuse to start when ``DISABLE_AUTH=true`` and prod-like signals are set.

    Called eagerly at FastAPI app startup so a misconfigured deployment
    crash-loops at boot rather than serving every endpoint anonymously.
    Operators who knowingly want anonymous access in a non-loopback
    environment must set ``I_KNOW_THIS_DISABLES_AUTH=yes-i-am-sure``.
    """
    if not settings.DISABLE_AUTH:
        return

    prod_signals: list[str] = []
    if not is_loopback_url(settings.SERVER_URL):
        prod_signals.append(f"SERVER_URL={settings.SERVER_URL!r} is not loopback")
    if settings.S3_BACKUP_ENABLED:
        prod_signals.append("S3_BACKUP_ENABLED=true")
    non_loopback_redirects = [
        p
        for p in settings.OAUTH_REDIRECT_URI_ALLOWLIST
        if p != "*" and not is_loopback_url(p)
    ]
    if non_loopback_redirects:
        prod_signals.append(
            f"OAUTH_REDIRECT_URI_ALLOWLIST contains non-loopback entries: {non_loopback_redirects}"
        )
    if "*" in settings.OAUTH_REDIRECT_URI_ALLOWLIST:
        prod_signals.append("OAUTH_REDIRECT_URI_ALLOWLIST contains wildcard '*'")

    if not prod_signals:
        return

    if settings.DISABLE_AUTH_CONFIRM == "yes-i-am-sure":
        logger.warning(
            "DISABLE_AUTH=true with production signals %s, but "
            "I_KNOW_THIS_DISABLES_AUTH=yes-i-am-sure is set. Proceeding.",
            prod_signals,
        )
        return

    raise RuntimeError(
        "DISABLE_AUTH=true is set alongside production signals: "
        + "; ".join(prod_signals)
        + ". Refusing to start to avoid serving the API anonymously. "
        "If this is genuinely a development environment, switch "
        "SERVER_URL to localhost / disable S3 backup / restrict the "
        "OAuth redirect allowlist to loopback. To override anyway, "
        "set I_KNOW_THIS_DISABLES_AUTH=yes-i-am-sure."
    )
