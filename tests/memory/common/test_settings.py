"""Tests for memory.common.settings env-reading helpers."""

from types import SimpleNamespace

import pytest

from memory.common.settings import (
    OAUTH_LOOPBACK_DEFAULTS,
    build_redirect_allowlist,
    missing_required_settings,
    require_settings,
    secret_env,
)


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


# --- build_redirect_allowlist (additive OAuth redirect allowlist) ----------


def test_build_redirect_allowlist_empty_returns_loopback_defaults():
    assert build_redirect_allowlist("") == OAUTH_LOOPBACK_DEFAULTS


def test_build_redirect_allowlist_adds_origin_to_defaults():
    assert build_redirect_allowlist("https://app.example.com") == [
        *OAUTH_LOOPBACK_DEFAULTS,
        "https://app.example.com",
    ]


def test_build_redirect_allowlist_dedupes_listed_loopback():
    # Operator who also lists localhost must not get a duplicate entry, and
    # order is preserved (defaults first).
    assert build_redirect_allowlist(
        "http://localhost,https://app.example.com"
    ) == [*OAUTH_LOOPBACK_DEFAULTS, "https://app.example.com"]


def test_build_redirect_allowlist_star_disables_check():
    assert build_redirect_allowlist("*") == ["*"]


def test_build_redirect_allowlist_strips_whitespace_and_blanks():
    assert build_redirect_allowlist("  https://app.example.com , , ") == [
        *OAUTH_LOOPBACK_DEFAULTS,
        "https://app.example.com",
    ]


# --- required settings startup check -----------------------------------------

COMPLETE_SETTINGS = dict(
    VOYAGE_API_KEY="voyage",
    ANTHROPIC_API_KEY="",
    OPENAI_API_KEY="",
    SUMMARIZER_MODEL="anthropic/claude-haiku-5-5",
)


@pytest.mark.parametrize(
    "role, overrides, expected",
    [
        ("worker", {"ANTHROPIC_API_KEY": "sk"}, []),
        ("worker", {}, ["ANTHROPIC_API_KEY"]),
        ("worker", {"SUMMARIZER_MODEL": "openai/gpt-5", "OPENAI_API_KEY": "sk"}, []),
        ("worker", {"SUMMARIZER_MODEL": "openai/gpt-5"}, ["OPENAI_API_KEY"]),
        ("worker", {"VOYAGE_API_KEY": "", "ANTHROPIC_API_KEY": "sk"}, ["VOYAGE_API_KEY"]),
        ("api", {}, []),
        ("api", {"VOYAGE_API_KEY": ""}, ["VOYAGE_API_KEY"]),
    ],
)
def test_missing_required_settings(role, overrides, expected):
    values = SimpleNamespace(**{**COMPLETE_SETTINGS, **overrides})
    assert missing_required_settings(values, role) == expected


def test_require_settings_lists_every_missing_setting(capsys):
    values = SimpleNamespace(**{**COMPLETE_SETTINGS, "VOYAGE_API_KEY": ""})
    with pytest.raises(SystemExit) as exc:
        require_settings(values, "worker")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "VOYAGE_API_KEY" in err
    assert "ANTHROPIC_API_KEY" in err


def test_require_settings_rejects_unknown_role(capsys):
    values = SimpleNamespace(**{**COMPLETE_SETTINGS, "ANTHROPIC_API_KEY": "sk"})
    with pytest.raises(SystemExit) as exc:
        require_settings(values, "wroker")
    assert exc.value.code == 2
    assert "wroker" in capsys.readouterr().err


def test_require_settings_passes_when_complete():
    require_settings(SimpleNamespace(**{**COMPLETE_SETTINGS, "ANTHROPIC_API_KEY": "sk"}), "worker")
