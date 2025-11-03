from datetime import datetime, timedelta, timezone
from typing import Iterable

import pytest

try:
    import redis  # noqa: F401  # pragma: no cover - optional test dependency
except ModuleNotFoundError:  # pragma: no cover - import guard for test envs
    import sys
    from types import SimpleNamespace

    class _RedisStub(SimpleNamespace):
        class Redis:  # type: ignore[no-redef]
            def __init__(self, *args: object, **kwargs: object) -> None:
                raise ModuleNotFoundError(
                    "The 'redis' package is required to use RedisUsageTracker"
                )

    sys.modules.setdefault("redis", _RedisStub())

from memory.common.llms.usage import (
    InMemoryUsageTracker,
    RateLimitConfig,
    RedisUsageTracker,
    UsageTracker,
)


class FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str) -> None:
        self._store[key] = value

    def scan_iter(self, match: str) -> Iterable[str]:
        from fnmatch import fnmatch

        for key in list(self._store.keys()):
            if fnmatch(key, match):
                yield key


@pytest.fixture
def tracker() -> InMemoryUsageTracker:
    config = RateLimitConfig(
        window=timedelta(minutes=1),
        max_input_tokens=1_000,
        max_output_tokens=2_000,
        max_total_tokens=2_500,
    )
    return InMemoryUsageTracker(
        {
            "anthropic/claude-3": config,
            "anthropic/haiku": config,
        }
    )


@pytest.fixture
def redis_tracker() -> RedisUsageTracker:
    config = RateLimitConfig(
        window=timedelta(minutes=1),
        max_input_tokens=1_000,
        max_output_tokens=2_000,
        max_total_tokens=2_500,
    )
    return RedisUsageTracker(
        {
            "anthropic/claude-3": config,
            "anthropic/haiku": config,
        },
        redis_client=FakeRedis(),
    )


@pytest.mark.parametrize(
    "window, kwargs",
    [
        (timedelta(minutes=1), {}),
        (timedelta(seconds=0), {"max_total_tokens": 1}),
    ],
)
def test_rate_limit_config_validation(
    window: timedelta, kwargs: dict[str, int]
) -> None:
    with pytest.raises(ValueError):
        RateLimitConfig(window=window, **kwargs)


def test_allows_usage_within_limits(tracker: InMemoryUsageTracker) -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    tracker.record_usage("anthropic/claude-3", 100, 200, timestamp=now)

    allowance = tracker.get_available_tokens("anthropic/claude-3", timestamp=now)
    assert allowance is not None
    assert allowance.input_tokens == 900
    assert allowance.output_tokens == 1_800
    assert allowance.total_tokens == 2_200


def test_rate_limited_when_over_budget(tracker: InMemoryUsageTracker) -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    tracker.record_usage("anthropic/claude-3", 800, 1_700, timestamp=now)

    assert tracker.is_rate_limited("anthropic/claude-3", timestamp=now)


def test_recovers_after_window(tracker: InMemoryUsageTracker) -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    tracker.record_usage("anthropic/claude-3", 800, 1_700, timestamp=now)

    later = now + timedelta(minutes=2)
    allowance = tracker.get_available_tokens("anthropic/claude-3", timestamp=later)
    assert allowance is not None
    assert allowance.input_tokens == 1_000
    assert allowance.output_tokens == 2_000
    assert allowance.total_tokens == 2_500
    assert not tracker.is_rate_limited("anthropic/claude-3", timestamp=later)


def test_usage_breakdown_and_provider_totals(tracker: InMemoryUsageTracker) -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    # Use the configured models from the fixture
    tracker.record_usage("anthropic/claude-3", 100, 200, timestamp=now)
    tracker.record_usage("anthropic/haiku", 50, 75, timestamp=now)

    breakdown = tracker.get_usage_breakdown()
    assert "anthropic" in breakdown
    assert "claude-3" in breakdown["anthropic"]
    claude_usage = breakdown["anthropic"]["claude-3"]
    assert claude_usage.window_input_tokens == 100
    assert claude_usage.window_output_tokens == 200

    provider_totals = dict(tracker.iter_provider_totals())
    anthropic_totals = provider_totals["anthropic"]
    assert anthropic_totals.window_input_tokens == 150
    assert anthropic_totals.window_output_tokens == 275


def test_get_usage_breakdown_filters(tracker: InMemoryUsageTracker) -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    # Use configured models from the fixture
    tracker.record_usage("anthropic/claude-3", 10, 20, timestamp=now)
    tracker.record_usage("openai/gpt-4o", 5, 5, timestamp=now)

    filtered = tracker.get_usage_breakdown(provider="anthropic")
    assert set(filtered.keys()) == {"anthropic"}
    assert set(filtered["anthropic"].keys()) == {"claude-3"}

    filtered_model = tracker.get_usage_breakdown(model="gpt-4o")
    assert set(filtered_model.keys()) == {"openai"}
    assert set(filtered_model["openai"].keys()) == {"gpt-4o"}


def test_missing_configuration_uses_default() -> None:
    # With no specific config, falls back to default config (from settings)
    tracker = InMemoryUsageTracker(configs={})
    tracker.record_usage("openai/gpt-4o", 10, 20)

    # Uses default config, so get_available_tokens returns allowance
    allowance = tracker.get_available_tokens("openai/gpt-4o")
    assert allowance is not None

    # Lifetime stats are tracked
    breakdown = tracker.get_usage_breakdown()
    usage = breakdown["openai"]["gpt-4o"]
    assert usage.window_input_tokens == 10
    assert usage.lifetime_input_tokens == 10


def test_default_configuration_is_used() -> None:
    default = RateLimitConfig(window=timedelta(minutes=1), max_total_tokens=100)
    tracker = InMemoryUsageTracker(configs={}, default_config=default)

    tracker.record_usage("anthropic/claude-3", 10, 10)
    allowance = tracker.get_available_tokens("anthropic/claude-3")
    assert allowance is not None
    assert allowance.total_tokens == 80


def test_record_usage_rejects_negative_values(tracker: InMemoryUsageTracker) -> None:
    with pytest.raises(ValueError):
        tracker.record_usage("anthropic/claude-3", -1, 0)


def test_is_rate_limited_when_only_output_exceeds_limit() -> None:
    config = RateLimitConfig(window=timedelta(minutes=1), max_output_tokens=50)
    tracker = InMemoryUsageTracker({"openai/gpt-4o": config})

    tracker.record_usage("openai/gpt-4o", 0, 50)
    assert tracker.is_rate_limited("openai/gpt-4o")


def test_redis_usage_tracker_persists_state(redis_tracker: RedisUsageTracker) -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    # Use configured models from the fixture
    redis_tracker.record_usage("anthropic/claude-3", 100, 200, timestamp=now)
    redis_tracker.record_usage("anthropic/haiku", 50, 75, timestamp=now)

    allowance = redis_tracker.get_available_tokens("anthropic/claude-3", timestamp=now)
    assert allowance is not None
    assert allowance.input_tokens == 900

    breakdown = redis_tracker.get_usage_breakdown()
    assert "anthropic" in breakdown
    assert "claude-3" in breakdown["anthropic"]
    assert breakdown["anthropic"]["claude-3"].window_output_tokens == 200

    items = dict(redis_tracker.iter_state_items())
    assert set(items.keys()) == {"anthropic/claude-3", "anthropic/haiku"}


def test_usage_tracker_base_not_instantiable() -> None:
    class DummyTracker(UsageTracker):
        pass

    with pytest.raises(NotImplementedError):
        DummyTracker({}).record_usage("provider/model", 1, 1)
