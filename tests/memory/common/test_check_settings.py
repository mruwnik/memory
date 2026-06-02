from memory.common import settings


def test_check_defaults():
    assert settings.CHECK_LEASE_TTL_SEC == 3600
    assert settings.CHECK_JOB_TTL_SEC == 1209600
    assert settings.CHECK_QUEUE_MAX_DEPTH == 100
    assert settings.CHECK_MAX_TEXT_BYTES == 65536
    assert settings.CHECK_MAX_LONG_POLL_SEC == 30
    assert settings.CHECK_CALLBACK_TIMEOUT_SEC == 10
    assert settings.CHECK_CALLBACK_MAX_ATTEMPTS == 3
    assert settings.CHECK_MAX_REQUEUE_ATTEMPTS == 3
    assert settings.CHECK_RATE_LIMIT_PER_MIN == 60
    assert settings.CHECK_ALLOW_PRIVATE_CALLBACKS is False
    assert settings.CHECK_DEFAULT_WAIT_SEC == 60
    assert settings.CHECK_MAX_WAIT_SEC == 300
    assert settings.CHECK_WAIT_POLL_INTERVAL_SEC == 2.0
