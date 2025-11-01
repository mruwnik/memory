"""LLM usage tracking utilities."""

from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from memory.common import settings


@dataclass(frozen=True)
class RateLimitConfig:
    """Configuration for a single rolling usage window."""

    window: timedelta
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_total_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.window <= timedelta(0):
            raise ValueError("window must be positive")
        if (
            self.max_input_tokens is None
            and self.max_output_tokens is None
            and self.max_total_tokens is None
        ):
            raise ValueError(
                "At least one of max_input_tokens, max_output_tokens or "
                "max_total_tokens must be provided"
            )


@dataclass
class UsageEvent:
    timestamp: datetime
    input_tokens: int
    output_tokens: int


@dataclass
class UsageState:
    events: deque[UsageEvent] = field(default_factory=deque)
    window_input_tokens: int = 0
    window_output_tokens: int = 0
    lifetime_input_tokens: int = 0
    lifetime_output_tokens: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "events": [
                {
                    "timestamp": event.timestamp.isoformat(),
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                }
                for event in self.events
            ],
            "window_input_tokens": self.window_input_tokens,
            "window_output_tokens": self.window_output_tokens,
            "lifetime_input_tokens": self.lifetime_input_tokens,
            "lifetime_output_tokens": self.lifetime_output_tokens,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "UsageState":
        events = deque(
            UsageEvent(
                timestamp=datetime.fromisoformat(event["timestamp"]),
                input_tokens=event["input_tokens"],
                output_tokens=event["output_tokens"],
            )
            for event in payload.get("events", [])
        )
        return cls(
            events=events,
            window_input_tokens=payload.get("window_input_tokens", 0),
            window_output_tokens=payload.get("window_output_tokens", 0),
            lifetime_input_tokens=payload.get("lifetime_input_tokens", 0),
            lifetime_output_tokens=payload.get("lifetime_output_tokens", 0),
        )


@dataclass
class TokenAllowance:
    """Represents the tokens that can be consumed right now."""

    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


@dataclass
class UsageBreakdown:
    """Detailed usage statistics for a provider/model pair."""

    window_input_tokens: int
    window_output_tokens: int
    window_total_tokens: int
    lifetime_input_tokens: int
    lifetime_output_tokens: int

    @property
    def window_total(self) -> int:
        return self.window_total_tokens

    @property
    def lifetime_total_tokens(self) -> int:
        return self.lifetime_input_tokens + self.lifetime_output_tokens


def split_model_key(model: str) -> tuple[str, str]:
    if "/" not in model:
        raise ValueError(
            f"model must be formatted as '<provider>/<model_name>': got '{model}'"
        )

    provider, model_name = model.split("/", maxsplit=1)
    if not provider or not model_name:
        raise ValueError(
            f"model must include both provider and model name separated by '/': got '{model}'"
        )
    return provider, model_name


class UsageTracker:
    """Base class for usage trackers that operate on provider/model keys."""

    def __init__(
        self,
        configs: dict[str, RateLimitConfig] | None = None,
        default_config: RateLimitConfig | None = None,
    ) -> None:
        self._configs = configs or {}
        self._default_config = default_config or RateLimitConfig(
            window=timedelta(minutes=settings.DEFAULT_LLM_RATE_LIMIT_WINDOW_MINUTES),
            max_input_tokens=settings.DEFAULT_LLM_RATE_LIMIT_MAX_INPUT_TOKENS,
            max_output_tokens=settings.DEFAULT_LLM_RATE_LIMIT_MAX_OUTPUT_TOKENS,
        )
        self._lock = Lock()

    # ------------------------------------------------------------------
    # Storage hooks
    # ------------------------------------------------------------------
    def get_state(self, key: str) -> UsageState:
        raise NotImplementedError

    def iter_state_items(self) -> Iterable[tuple[str, UsageState]]:
        raise NotImplementedError

    def save_state(self, key: str, state: UsageState) -> None:
        """Persist the given state back to the underlying store."""
        del key, state

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def record_usage(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        timestamp: datetime | None = None,
    ) -> None:
        """Record token usage for the given provider/model pair."""

        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("Token counts must be non-negative")

        timestamp = timestamp or datetime.now(timezone.utc)
        split_model_key(model)
        key = model

        with self._lock:
            config = self._get_config(key)
            state = self.get_state(key)

            state.lifetime_input_tokens += input_tokens
            state.lifetime_output_tokens += output_tokens

            if config is None:
                self.save_state(key, state)
                return

            state.events.append(UsageEvent(timestamp, input_tokens, output_tokens))
            state.window_input_tokens += input_tokens
            state.window_output_tokens += output_tokens

            self._prune_expired_events(state, config, now=timestamp)
            self.save_state(key, state)

    def is_rate_limited(
        self,
        model: str,
        timestamp: datetime | None = None,
    ) -> bool:
        """Return True if the pair currently exceeds its limits."""

        allowance = self.get_available_tokens(model, timestamp=timestamp)
        if allowance is None:
            return False

        limits = [
            allowance.input_tokens,
            allowance.output_tokens,
            allowance.total_tokens,
        ]
        return any(limit is not None and limit <= 0 for limit in limits)

    def get_available_tokens(
        self,
        model: str,
        timestamp: datetime | None = None,
    ) -> TokenAllowance | None:
        """Return the current token allowance for the provider/model pair.

        If there is no configuration for the pair (or a default configuration),
        ``None`` is returned to indicate that no limits are enforced.
        """

        split_model_key(model)
        with self._lock:
            config = self._get_config(model)
            if config is None:
                return None

            state = self.get_state(model)
            self._prune_expired_events(state, config, now=timestamp)
            self.save_state(model, state)

            if config.max_total_tokens is None:
                total_remaining = None
            else:
                total_remaining = config.max_total_tokens - (
                    state.window_input_tokens + state.window_output_tokens
                )

            if config.max_input_tokens is None:
                input_remaining = None
            else:
                input_remaining = config.max_input_tokens - state.window_input_tokens

            if config.max_output_tokens is None:
                output_remaining = None
            else:
                output_remaining = config.max_output_tokens - state.window_output_tokens

            return TokenAllowance(
                input_tokens=clamp_non_negative(input_remaining),
                output_tokens=clamp_non_negative(output_remaining),
                total_tokens=clamp_non_negative(total_remaining),
            )

    def get_usage_breakdown(
        self, provider: str | None = None, model: str | None = None
    ) -> dict[str, dict[str, UsageBreakdown]]:
        """Return usage statistics grouped by provider and model."""

        with self._lock:
            providers: dict[str, dict[str, UsageBreakdown]] = defaultdict(dict)
            for model, state in self.iter_state_items():
                prov, model_name = split_model_key(model)
                if provider and provider != prov:
                    continue
                if model and model != model_name:
                    continue

                window_total = state.window_input_tokens + state.window_output_tokens
                breakdown = UsageBreakdown(
                    window_input_tokens=state.window_input_tokens,
                    window_output_tokens=state.window_output_tokens,
                    window_total_tokens=window_total,
                    lifetime_input_tokens=state.lifetime_input_tokens,
                    lifetime_output_tokens=state.lifetime_output_tokens,
                )
                providers[prov][model_name] = breakdown

            return providers

    def iter_provider_totals(self) -> Iterable[tuple[str, UsageBreakdown]]:
        """Yield aggregated totals for each provider across its models."""

        breakdowns = self.get_usage_breakdown()
        for provider, models in breakdowns.items():
            window_input = sum(b.window_input_tokens for b in models.values())
            window_output = sum(b.window_output_tokens for b in models.values())
            lifetime_input = sum(b.lifetime_input_tokens for b in models.values())
            lifetime_output = sum(b.lifetime_output_tokens for b in models.values())

            yield (
                provider,
                UsageBreakdown(
                    window_input_tokens=window_input,
                    window_output_tokens=window_output,
                    window_total_tokens=window_input + window_output,
                    lifetime_input_tokens=lifetime_input,
                    lifetime_output_tokens=lifetime_output,
                ),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _get_config(self, model: str) -> RateLimitConfig | None:
        return self._configs.get(model) or self._default_config

    def _prune_expired_events(
        self,
        state: UsageState,
        config: RateLimitConfig,
        now: datetime | None = None,
    ) -> None:
        if not state.events:
            return

        now = now or datetime.now(timezone.utc)
        cutoff = now - config.window

        for event in tuple(state.events):
            if event.timestamp > cutoff:
                break
            state.events.popleft()
            state.window_input_tokens -= event.input_tokens
            state.window_output_tokens -= event.output_tokens

        state.window_input_tokens = max(state.window_input_tokens, 0)
        state.window_output_tokens = max(state.window_output_tokens, 0)


class InMemoryUsageTracker(UsageTracker):
    """Tracks LLM usage for providers and models within a rolling window."""

    def __init__(
        self,
        configs: dict[str, RateLimitConfig],
        default_config: RateLimitConfig | None = None,
    ) -> None:
        super().__init__(configs=configs, default_config=default_config)
        self._states: dict[str, UsageState] = {}

    def get_state(self, key: str) -> UsageState:
        return self._states.setdefault(key, UsageState())

    def iter_state_items(self) -> Iterable[tuple[str, UsageState]]:
        return tuple(self._states.items())


def clamp_non_negative(value: int | None) -> int | None:
    if value is None:
        return None
    return 0 if value < 0 else value
