"""Content-Security-Policy source-value sanitisation.

A CSP source value (one entry in `connect-src`, `script-src`, etc.) must not
contain characters that would let an attacker break out of the directive:

- ``;`` splits CSP directives. Injecting ``"evil.com; script-src *"`` would
  add a *new* directive that overrides the rest of the policy.
- ``\\r`` / ``\\n`` / ``\\0`` can split or terminate the HTTP header value.
- Space separates source expressions within a single directive, so a value
  containing one would silently expand to multiple sources.

This module is the single source of truth for that policy. Two call sites
(``MCP/servers/reports.py`` at write-time, ``api/app.py`` at serve-time)
previously kept their own copies of the forbidden-set and the predicate;
keeping them in sync was a maintenance hazard.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


CSP_FORBIDDEN_CHARS: frozenset[str] = frozenset({";", "\r", "\n", "\0", " "})


def is_safe_csp_source(value: str) -> bool:
    """Return True iff ``value`` is safe to embed in a CSP source list."""
    return not (set(value) & CSP_FORBIDDEN_CHARS)


def find_invalid_csp_sources(sources: list[str]) -> list[str]:
    """Return the subset of ``sources`` that fail :func:`is_safe_csp_source`.

    Used by the write-time validator (reject the whole input with a single
    error listing every bad value).
    """
    return [src for src in sources if not is_safe_csp_source(src)]


def sanitize_csp_source_list(sources: list[str]) -> list[str]:
    """Return ``sources`` with unsafe entries dropped, logging each.

    Used by the serve-time path: a stale or bad DB row should not break
    report serving entirely, so we drop it and log a warning.
    """
    clean: list[str] = []
    for src in sources:
        if is_safe_csp_source(src):
            clean.append(src)
        else:
            logger.warning("Dropped unsafe CSP source value: %r", src)
    return clean
