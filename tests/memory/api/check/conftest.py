import fakeredis.aioredis as fakeaioredis
import pytest


@pytest.fixture
def r():
    """Fresh in-memory async Redis per test (decode_responses=True)."""
    return fakeaioredis.FakeRedis(decode_responses=True)
