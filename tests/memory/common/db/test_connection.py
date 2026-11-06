import pytest
from sqlalchemy import literal, select
from sqlalchemy.dialects.postgresql import JSONB

from memory.common.db.connection import get_engine, strip_nul


@pytest.mark.parametrize(
    "value, expected",
    [
        ("a\x00b", "ab"),
        (None, None),
        (3, 3),
        (b"a\x00b", b"a\x00b"),
        (memoryview(b"a\x00b"), memoryview(b"a\x00b")),
        (("a\x00", 1), ("a", 1)),
        (["\x00", ("b\x00",)], ["", ("b",)]),
        ({"k\x00": {"n": ["x\x00y", None]}}, {"k": {"n": ["xy", None]}}),
    ],
)
def test_strip_nul(value, expected):
    result = strip_nul(value)
    assert result == expected
    assert type(result) is type(expected)


def test_jsonb_values_with_nul_are_stored_without_it(test_db):
    with get_engine().connect() as conn:
        stored = conn.execute(select(literal({"title": "a\x00b"}, JSONB))).scalar()
    assert stored == {"title": "ab"}
