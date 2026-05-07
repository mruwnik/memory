"""Tests for the canonical ISO-8601 datetime parser."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memory.common.dates import parse_iso_datetime, parse_iso_datetime_utc


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-05-07T12:34:56Z", datetime(2026, 5, 7, 12, 34, 56, tzinfo=timezone.utc)),
        (
            "2026-05-07T12:34:56+00:00",
            datetime(2026, 5, 7, 12, 34, 56, tzinfo=timezone.utc),
        ),
        ("2026-05-07T12:34:56", datetime(2026, 5, 7, 12, 34, 56)),  # naive
        (
            "2026-05-07T12:34:56.500Z",
            datetime(2026, 5, 7, 12, 34, 56, 500000, tzinfo=timezone.utc),
        ),
        (
            "2026-05-07T12:34:56-05:00",
            datetime(2026, 5, 7, 17, 34, 56, tzinfo=timezone.utc),
        ),
        ("2026-05-07", datetime(2026, 5, 7)),  # date-only also accepted
    ],
)
def test_parse_iso_datetime_valid(raw, expected):
    parsed = parse_iso_datetime(raw)
    assert parsed is not None
    if expected.tzinfo is not None:
        assert parsed.astimezone(timezone.utc) == expected
    else:
        # Compare without tz for naive expected
        assert parsed.replace(tzinfo=None) == expected.replace(tzinfo=None)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "not a date",
        "2026-13-01T00:00:00Z",  # month 13
        "2026-05-32T00:00:00Z",  # day 32
        "yesterday",
    ],
)
def test_parse_iso_datetime_returns_none(raw):
    assert parse_iso_datetime(raw) is None


def test_parse_iso_datetime_handles_non_string():
    """Pass-through callers occasionally hand the helper a non-str (e.g.
    a numeric Unix timestamp). Don't crash; return None."""
    assert parse_iso_datetime(12345) is None  # type: ignore[arg-type]


def test_parse_iso_datetime_utc_normalises_naive():
    """Naive datetimes get tagged with UTC; tz-aware ones pass through."""
    naive = parse_iso_datetime_utc("2026-05-07T12:34:56")
    assert naive == datetime(2026, 5, 7, 12, 34, 56, tzinfo=timezone.utc)

    aware = parse_iso_datetime_utc("2026-05-07T12:34:56-05:00")
    assert aware == datetime(2026, 5, 7, 17, 34, 56, tzinfo=timezone.utc)


def test_parse_iso_datetime_utc_propagates_none():
    """Bad / empty input still returns None."""
    assert parse_iso_datetime_utc(None) is None
    assert parse_iso_datetime_utc("") is None
    assert parse_iso_datetime_utc("garbage") is None


def test_parse_iso_datetime_z_and_plus00_equivalent():
    """The trailing-Z and +00:00 forms must produce identical datetimes —
    that's the entire reason the helper exists."""
    assert parse_iso_datetime("2026-05-07T12:34:56Z") == parse_iso_datetime(
        "2026-05-07T12:34:56+00:00"
    )
