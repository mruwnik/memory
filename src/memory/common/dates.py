"""Canonical ISO-8601 datetime parsing.

Many call sites in the codebase used to inline
``datetime.fromisoformat(s.replace("Z", "+00:00"))`` to handle the
trailing-``Z`` UTC form emitted by GitHub, Anthropic transcripts, web
APIs, etc. The replace-then-parse idiom was duplicated in 15+ places
and at least two competing helpers (``parse_github_date`` in
``common/github/types.py`` and ``parse_datetime`` in
``api/MCP/servers/polling.py``) implemented it with different
error-handling conventions.

This module is the single source of truth.
"""

from __future__ import annotations

from datetime import datetime, timezone


def parse_iso_datetime(s: str | None) -> datetime | None:
    """Parse an ISO-8601 datetime, accepting the trailing-Z UTC form.

    Returns ``None`` for missing / empty / unparseable input. Caller
    must decide whether to raise or default — we don't make that
    decision here so the helper is reusable across both
    "garbage-in-None-out" and "raise on bad input" call sites.
    """
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def parse_iso_datetime_utc(s: str | None) -> datetime | None:
    """Like ``parse_iso_datetime`` but normalises naive results to UTC.

    Useful at boundaries where downstream code compares timestamps with
    ``datetime.now(timezone.utc)`` and would crash on naive values.
    """
    dt = parse_iso_datetime(s)
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
