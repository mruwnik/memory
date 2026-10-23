from decimal import Decimal

import pytest

from memory.agents.caps import effective_cap

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
