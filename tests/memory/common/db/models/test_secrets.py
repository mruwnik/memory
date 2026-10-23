"""Tests for secret extract/log-safety helpers."""

import logging
from unittest.mock import MagicMock

from memory.common.db.models import secrets


def test_extract_does_not_log_literal_value(caplog):
    """When `name` is a literal value (no matching secret), extract must
    NOT include `name` in the log message — it could be a real secret
    (e.g. a GitHub PAT passed through cloud_claude's GITHUB_TOKEN
    plumbing). See secrets.py:extract for the contract."""
    fake_session = MagicMock()
    # find_secret() returns None → fallback path triggered.
    fake_session.query.return_value.filter.return_value.first.return_value = None

    literal_secret = "ghp_THIS_IS_A_SECRET_VALUE_DO_NOT_LOG_ME"

    with caplog.at_level(logging.DEBUG, logger=secrets.logger.name):
        result = secrets.extract(fake_session, user_id=42, name=literal_secret)

    assert result == literal_secret
    # The whole log buffer must not contain the literal secret value.
    full_log = "\n".join(record.getMessage() for record in caplog.records)
    assert literal_secret not in full_log
    assert "ghp_" not in full_log


def test_key_derivation_runs_once_per_secret(monkeypatch):
    derived = []
    real = secrets.PBKDF2HMAC

    def counting(*args, **kwargs):
        derived.append(kwargs["salt"])
        return real(*args, **kwargs)

    monkeypatch.setattr(secrets, "PBKDF2HMAC", counting)
    monkeypatch.setattr(secrets.settings, "SECRETS_ENCRYPTION_KEY", "key-derive-once")
    secrets.derive_encryption_key.cache_clear()

    tokens = [secrets.encrypt_value(f"token-{i}") for i in range(3)]

    assert [secrets.decrypt_value(t) for t in tokens] == ["token-0", "token-1", "token-2"]
    assert len(derived) == 1


def test_a_changed_secret_derives_a_new_key(monkeypatch):
    monkeypatch.setattr(secrets.settings, "SECRETS_ENCRYPTION_KEY", "first")
    first = secrets.get_encryption_key()
    monkeypatch.setattr(secrets.settings, "SECRETS_ENCRYPTION_KEY", "second")

    assert secrets.get_encryption_key() != first
