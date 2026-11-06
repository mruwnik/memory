"""Tests for memory.common.settings env-reading helpers."""

import logging
from types import SimpleNamespace

import pytest

from memory.common.settings import (
    OAUTH_REDIRECT_DEFAULTS,
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


def test_build_redirect_allowlist_empty_returns_defaults():
    assert build_redirect_allowlist("") == OAUTH_REDIRECT_DEFAULTS


@pytest.mark.parametrize(
    "origin",
    ["http://localhost", "http://127.0.0.1", "https://claude.ai", "https://claude.com", "https://chatgpt.com", "https://grok.com"],
)
def test_build_redirect_allowlist_allows_loopback_and_hosted_connectors_unconfigured(origin):
    assert origin in build_redirect_allowlist("")


def test_build_redirect_allowlist_adds_origin_to_defaults():
    assert build_redirect_allowlist("https://app.example.com") == [
        *OAUTH_REDIRECT_DEFAULTS,
        "https://app.example.com",
    ]


def test_build_redirect_allowlist_dedupes_listed_loopback():
    # Operator who also lists localhost must not get a duplicate entry, and
    # order is preserved (defaults first).
    assert build_redirect_allowlist(
        "http://localhost,https://app.example.com"
    ) == [*OAUTH_REDIRECT_DEFAULTS, "https://app.example.com"]


def test_build_redirect_allowlist_star_disables_check():
    assert build_redirect_allowlist("*") == ["*"]


def test_build_redirect_allowlist_strips_whitespace_and_blanks():
    assert build_redirect_allowlist("  https://app.example.com , , ") == [
        *OAUTH_REDIRECT_DEFAULTS,
        "https://app.example.com",
    ]


@pytest.mark.parametrize(
    "env_value, server_url, expected_extra",
    [
        ("", "https://kb.example.com", ["https://kb.example.com"]),
        ("", "https://kb.example.com/", ["https://kb.example.com"]),
        ("", "https://kb.example.com:8443/base", ["https://kb.example.com:8443"]),
        (
            "https://app.example.com",
            "https://kb.example.com",
            ["https://app.example.com", "https://kb.example.com"],
        ),
        ("https://kb.example.com", "https://kb.example.com", ["https://kb.example.com"]),
        ("", "", []),
        ("", "http://localhost:8000", ["http://localhost:8000"]),
        ("*", "https://kb.example.com", None),
    ],
)
def test_build_redirect_allowlist_always_allows_server_origin(
    env_value, server_url, expected_extra
):
    expected = (
        ["*"] if expected_extra is None else [*OAUTH_REDIRECT_DEFAULTS, *expected_extra]
    )
    assert build_redirect_allowlist(env_value, server_url) == expected


# --- required settings startup check -----------------------------------------

COMPLETE_SETTINGS = dict(
    VOYAGE_API_KEY="voyage",
    CELERY_QUEUE_PREFIX="memory",
    QUEUE_PREFIX="",
    S3_BACKUP_BUCKET="",
    BACKUP_ENCRYPTION_KEY="",
    ENABLE_MODULES=frozenset(),
    DISABLE_MODULES=frozenset(),
    NOTES_SYNC_INTERVAL=None,
)

@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({}, []),
        # An admin can set it in the UI.
        ({"VOYAGE_API_KEY": ""}, []),
        ({"S3_BACKUP_BUCKET": "b"}, ["BACKUP_ENCRYPTION_KEY"]),
        ({"S3_BACKUP_BUCKET": "b", "BACKUP_ENCRYPTION_KEY": "k"}, []),
        ({"BACKUP_ENCRYPTION_KEY": "k"}, []),
    ],
)
def test_missing_required_settings(overrides, expected):
    values = SimpleNamespace(**{**COMPLETE_SETTINGS, **overrides})
    assert missing_required_settings(values) == expected


def test_bucket_without_key_names_both_variables_but_no_values(capsys):
    values = SimpleNamespace(
        **{**COMPLETE_SETTINGS, "S3_BACKUP_BUCKET": "my-bucket"}
    )
    with pytest.raises(SystemExit) as exc:
        require_settings(values, "worker")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "S3_BACKUP_BUCKET" in err
    assert "BACKUP_ENCRYPTION_KEY" in err
    assert "my-bucket" not in err


def test_removed_s3_backup_enabled_refuses_to_start(monkeypatch, capsys):
    monkeypatch.setenv("S3_BACKUP_ENABLED", "false")
    values = SimpleNamespace(**COMPLETE_SETTINGS)
    with pytest.raises(SystemExit) as exc:
        require_settings(values, "worker")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "S3_BACKUP_ENABLED" in err
    assert "S3_BACKUP_BUCKET" in err


def test_require_settings_rejects_unknown_role(capsys):
    values = SimpleNamespace(**COMPLETE_SETTINGS)
    with pytest.raises(SystemExit) as exc:
        require_settings(values, "wroker")
    assert exc.value.code == 2
    assert "wroker" in capsys.readouterr().err


def test_require_settings_passes_when_complete():
    require_settings(SimpleNamespace(**COMPLETE_SETTINGS), "worker")


@pytest.mark.parametrize(
    "worker_prefix,task_prefix", [("", "memory"), ("memory", "memory"), ("chris", "chris")]
)
def test_workers_on_the_routed_queues_start(worker_prefix, task_prefix):
    values = SimpleNamespace(
        **{
            **COMPLETE_SETTINGS,
            "QUEUE_PREFIX": worker_prefix,
            "CELERY_QUEUE_PREFIX": task_prefix,
        }
    )

    require_settings(values, "worker")


@pytest.mark.parametrize(
    "worker_prefix,task_prefix,listens",
    [
        ("chris", "memory", "'chris'"),
        # entry.sh falls back to "memory" while tasks follow APP_NAME.
        ("", "chris", "'memory'"),
    ],
)
def test_a_worker_listening_on_other_queues_refuses_to_start(
    capsys, worker_prefix, task_prefix, listens
):
    values = SimpleNamespace(
        **{
            **COMPLETE_SETTINGS,
            "QUEUE_PREFIX": worker_prefix,
            "CELERY_QUEUE_PREFIX": task_prefix,
        }
    )

    with pytest.raises(SystemExit) as exc:
        require_settings(values, "worker")

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert f"listens on {listens}" in err
    assert f"CELERY_QUEUE_PREFIX '{task_prefix}'" in err


@pytest.mark.parametrize(
    "enable, disable, named",
    [({"tems"}, set(), "tems"), ({"teams"}, {"teams"}, "teams")],
)
def test_bad_module_config_refuses_to_start(capsys, enable, disable, named):
    values = SimpleNamespace(
        **{
            **COMPLETE_SETTINGS,
            "ANTHROPIC_API_KEY": "sk",
            "ENABLE_MODULES": frozenset(enable),
            "DISABLE_MODULES": frozenset(disable),
        }
    )

    with pytest.raises(SystemExit) as exc:
        require_settings(values, "api")

    assert exc.value.code == 1
    assert named in capsys.readouterr().err


@pytest.mark.parametrize("role", ["api", "worker"])
@pytest.mark.parametrize("env_var", ["ENABLE_MODULES", "DISABLE_MODULES"])
def test_retired_claude_containers_module_warns_and_starts(caplog, role, env_var):
    values = SimpleNamespace(
        **{**COMPLETE_SETTINGS, env_var: frozenset({"claude_containers", "teams"})}
    )

    with caplog.at_level(logging.WARNING, logger="memory.common.settings"):
        require_settings(values, role)

    assert "claude_containers" in caplog.text


@pytest.mark.parametrize("interval", [0, 59, 24 * 60 * 60 + 1, 10**9])
def test_an_out_of_range_notes_sync_interval_refuses_to_start(capsys, interval):
    values = SimpleNamespace(**{**COMPLETE_SETTINGS, "NOTES_SYNC_INTERVAL": interval})
    with pytest.raises(SystemExit) as exc:
        require_settings(values, "worker")
    assert exc.value.code == 1
    assert "NOTES_SYNC_INTERVAL" in capsys.readouterr().err


@pytest.mark.parametrize("interval", [None, 60, 24 * 60 * 60])
def test_an_in_range_notes_sync_interval_starts(interval):
    require_settings(SimpleNamespace(**{**COMPLETE_SETTINGS, "NOTES_SYNC_INTERVAL": interval}), "worker")
