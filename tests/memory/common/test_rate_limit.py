"""Tests for the Redis-backed rate limiter used on auth endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from memory.common import rate_limit


@pytest.fixture(autouse=True)
def reset_rate_limit_cache():
    """Each test gets a fresh module-level Redis cache state."""
    rate_limit.reset_cache()
    yield
    rate_limit.reset_cache()


# ====== parse_limit ======


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("10/minute", (10, 60)),
        ("100/hour", (100, 3600)),
        ("5/second", (5, 1)),
        ("3/day", (3, 86400)),
        ("10/minutes", (10, 60)),  # plural also accepted
        ("  10 / minute  ", (10, 60)),  # whitespace-tolerant
        ("10/MINUTE", (10, 60)),  # case-insensitive
    ],
)
def test_parse_limit_accepts_slowapi_style(spec, expected):
    assert rate_limit.parse_limit(spec) == expected


@pytest.mark.parametrize("bad", ["", "10", "/minute", "ten/minute", "10/decade", "10/"])
def test_parse_limit_rejects_garbage(bad):
    with pytest.raises(ValueError):
        rate_limit.parse_limit(bad)


# ====== check_rate_limit ======


def _stub_client(initial_count: int = 0) -> MagicMock:
    """Build a redis.Redis double whose pipeline().incr+expire+execute returns
    [count, True], simulating the real pipeline shape."""
    client = MagicMock()
    counter = {"n": initial_count}

    pipe = MagicMock()

    def incr(key, amount):
        counter["n"] += amount
        return pipe

    def expire(key, ttl):
        return pipe

    def execute():
        return [counter["n"], True]

    pipe.incr.side_effect = incr
    pipe.expire.side_effect = expire
    pipe.execute.side_effect = execute
    client.pipeline.return_value = pipe
    client.ping.return_value = True
    return client


def test_check_rate_limit_allows_under_limit():
    client = _stub_client()
    with patch.object(rate_limit, "get_redis", return_value=client):
        for _ in range(5):
            assert rate_limit.check_rate_limit("k", limit=5, window_seconds=60) is True


def test_check_rate_limit_blocks_over_limit():
    client = _stub_client()
    with patch.object(rate_limit, "get_redis", return_value=client):
        results = [
            rate_limit.check_rate_limit("k", limit=3, window_seconds=60)
            for _ in range(5)
        ]
    assert results == [True, True, True, False, False]


def test_check_rate_limit_fails_open_when_redis_unavailable():
    """Redis outage must not lock users out of authentication."""
    with patch.object(rate_limit, "get_redis", return_value=None):
        for _ in range(100):
            assert rate_limit.check_rate_limit("k", 10, 60) is True


def test_check_rate_limit_fails_open_on_redis_error():
    client = MagicMock()
    client.pipeline.side_effect = Exception("redis down mid-request")
    with patch.object(rate_limit, "get_redis", return_value=client):
        assert rate_limit.check_rate_limit("k", 10, 60) is True


def test_check_rate_limit_disabled_globally(monkeypatch):
    """API_RATE_LIMIT_ENABLED=False must not even contact Redis."""
    monkeypatch.setattr(rate_limit.settings, "API_RATE_LIMIT_ENABLED", False)
    client = MagicMock()
    with patch.object(rate_limit, "get_redis", return_value=client):
        assert rate_limit.check_rate_limit("k", 1, 60) is True
    client.pipeline.assert_not_called()


def test_check_rate_limit_keys_are_per_window(monkeypatch):
    """Fixed-window: incrementing in window N shouldn't affect window N+1."""
    client = _stub_client()
    pipe = client.pipeline.return_value

    keys_seen: list[str] = []
    real_incr = pipe.incr.side_effect

    def capture_incr(key, amount):
        keys_seen.append(key)
        return real_incr(key, amount)

    pipe.incr.side_effect = capture_incr

    with patch.object(rate_limit, "get_redis", return_value=client):
        with patch.object(rate_limit, "time") as mock_time:
            mock_time.time.return_value = 0
            rate_limit.check_rate_limit("user", 10, 60)  # window 0
            mock_time.time.return_value = 60
            rate_limit.check_rate_limit("user", 10, 60)  # window 1

    assert keys_seen[0].endswith(":0")
    assert keys_seen[1].endswith(":1")


# ====== check_rate_limit_spec ======


def test_check_rate_limit_spec_parses_and_enforces():
    client = _stub_client()
    with patch.object(rate_limit, "get_redis", return_value=client):
        results = [
            rate_limit.check_rate_limit_spec("k", "2/minute") for _ in range(4)
        ]
    assert results == [True, True, False, False]


# ====== get_redis caching ======


def test_get_redis_caches_failure_for_session(monkeypatch):
    """If Redis is unavailable on first call, don't reconnect on every check."""
    attempts = {"n": 0}

    def boom(*_a, **_kw):
        attempts["n"] += 1
        raise ConnectionError("nope")

    monkeypatch.setattr(rate_limit.redis.Redis, "from_url", classmethod(boom))

    assert rate_limit.get_redis() is None
    assert rate_limit.get_redis() is None
    assert rate_limit.get_redis() is None
    assert attempts["n"] == 1


def test_get_redis_caches_success(monkeypatch):
    client = MagicMock()
    client.ping.return_value = True
    attempts = {"n": 0}

    def make(*_a, **_kw):
        attempts["n"] += 1
        return client

    monkeypatch.setattr(rate_limit.redis.Redis, "from_url", classmethod(make))

    a = rate_limit.get_redis()
    b = rate_limit.get_redis()
    assert a is b
    assert attempts["n"] == 1
