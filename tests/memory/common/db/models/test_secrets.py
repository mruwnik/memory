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
