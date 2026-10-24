from decimal import Decimal

import pytest

from memory.agents.caps import effective_cap, per_run_cap
from memory.common import settings

D = Decimal


@pytest.mark.parametrize(
    "values,expected",
    [
        ((None, None), D("1")),
        ((D("0.25"), None), D("0.25")),
        ((D("5"), None), D("1")),
        ((D("0.5"), D("0.2"), D("0.9")), D("0.2")),
    ],
)
def test_effective_cap_strictest_wins_and_unset_is_ignored(values, expected):
    assert effective_cap(D("1"), *values) == expected


@pytest.mark.parametrize(
    "values,expected",
    [
        ((None,), D("1.00")),  # AGENT_DEFAULT_USER_KEY_RUN_COST_USD
        ((D("3"),), D("3")),  # an agent's own cap is not held to any server ceiling
        ((D("3"), D("0.5")), D("0.5")),
    ],
)
def test_per_run_cap_is_the_agents_cap_or_the_user_key_default(monkeypatch, values, expected):
    monkeypatch.setattr(settings, "AGENT_DEFAULT_USER_KEY_RUN_COST_USD", D("1.00"))
    assert per_run_cap(*values) == expected
