"""Tests for memory.common.settings env-reading helpers."""

import pytest

from memory.common.settings import secret_env


# --- secret_env (file or env var fallback) ---------------------------------


def test_secret_env_reads_plain_env_var(monkeypatch):
    monkeypatch.delenv("MY_SECRET_FILE", raising=False)
    monkeypatch.setenv("MY_SECRET", "plain-value")
    assert secret_env("MY_SECRET") == "plain-value"


@pytest.mark.parametrize("default,expected", [("", ""), ("fallback", "fallback")])
def test_secret_env_returns_default_when_unset(monkeypatch, default, expected):
    monkeypatch.delenv("MY_SECRET", raising=False)
    monkeypatch.delenv("MY_SECRET_FILE", raising=False)
    assert secret_env("MY_SECRET", default) == expected


def test_secret_env_reads_from_file(monkeypatch, tmp_path):
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("file-value")
    monkeypatch.setenv("MY_SECRET_FILE", str(secret_file))
    monkeypatch.delenv("MY_SECRET", raising=False)
    assert secret_env("MY_SECRET") == "file-value"


def test_secret_env_file_takes_priority_over_env(monkeypatch, tmp_path):
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("file-value")
    monkeypatch.setenv("MY_SECRET_FILE", str(secret_file))
    monkeypatch.setenv("MY_SECRET", "env-value")
    assert secret_env("MY_SECRET") == "file-value"


def test_secret_env_strips_file_whitespace(monkeypatch, tmp_path):
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("  file-value\n\n")
    monkeypatch.setenv("MY_SECRET_FILE", str(secret_file))
    assert secret_env("MY_SECRET") == "file-value"
