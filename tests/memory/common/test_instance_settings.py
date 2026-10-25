import pytest
from cryptography.fernet import Fernet

from memory.api.MCP.servers import HIDDEN_MCP_SERVERS
from memory.common import instance_settings, settings
from memory.common.db.models import InstanceSetting
from memory.common.db.models.secrets import encrypt_value_with_key
from memory.common.instance_settings import (
    ENABLE_RERANKING,
    VOYAGE_API_KEY,
    decode_rows,
    resolve,
    save,
    stored_values_in,
)


@pytest.mark.parametrize(
    "setting, env, stored, expected",
    [
        (VOYAGE_API_KEY, "pa-env", {"voyage_api_key": "pa-ui"}, ("pa-env", "env")),
        (VOYAGE_API_KEY, "", {"voyage_api_key": "pa-ui"}, ("pa-ui", "ui")),
        (VOYAGE_API_KEY, "", {}, ("", "default")),
        (ENABLE_RERANKING, False, {"enable_reranking": True}, (False, "env")),
        (ENABLE_RERANKING, None, {"enable_reranking": False}, (False, "ui")),
        (ENABLE_RERANKING, None, {}, (True, "default")),
    ],
)
def test_env_wins_over_the_ui_value_which_wins_over_the_default(
    monkeypatch, setting, env, stored, expected
):
    monkeypatch.setattr(settings, setting.env_var, env)
    assert resolve(setting, lambda: stored) == expected


@pytest.mark.parametrize(
    "key, value, value_encrypted",
    [
        # Encrypted under a SECRETS_ENCRYPTION_KEY this process doesn't have.
        ("voyage_api_key", None, encrypt_value_with_key("pa-secret", Fernet.generate_key())),
        ("disabled_mcp_servers", "slack", None),
        ("enable_reranking", "yes", None),
    ],
    ids=["foreign_key", "not_a_list", "not_a_bool"],
)
def test_a_row_that_cant_be_read_counts_as_unset_and_is_logged_by_name_only(
    db_session, monkeypatch, caplog, key, value, value_encrypted
):
    monkeypatch.setattr(instance_settings, "_logged_unreadable", set())
    db_session.add(InstanceSetting(key=key, value=value, value_encrypted=value_encrypted))
    db_session.add(InstanceSetting(key="enable_hyde_expansion", value=True))
    db_session.flush()

    for _ in range(2):
        rows = db_session.query(InstanceSetting).all()
        assert decode_rows(rows) == ({"enable_hyde_expansion": True}, {key})

    assert [r.getMessage() for r in caplog.records if r.levelname == "ERROR"] == [
        f"Ignoring the stored value of {key}: {'InvalidToken' if value_encrypted else 'ValueError'}"
    ]


def test_live_reads_survive_a_secret_encrypted_under_another_key(
    db_session, admin_user, monkeypatch
):
    monkeypatch.setattr(instance_settings, "_logged_unreadable", set())
    monkeypatch.setattr(settings, "VOYAGE_API_KEY", "")
    monkeypatch.setattr(settings, "ENABLE_RERANKING", None)
    save(db_session, VOYAGE_API_KEY, "pa-secret-1234", admin_user.id)
    save(db_session, ENABLE_RERANKING, False, admin_user.id)
    db_session.flush()

    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY", "1" * 64)
    assert (instance_settings.value(VOYAGE_API_KEY), instance_settings.value(ENABLE_RERANKING)) == ("", False)


@pytest.mark.parametrize(
    "setting, raw",
    [
        (ENABLE_RERANKING, "yes"),
        (VOYAGE_API_KEY, "  "),
        (VOYAGE_API_KEY, 12),
        (HIDDEN_MCP_SERVERS, "slack"),
        (HIDDEN_MCP_SERVERS, ["slack", "nope"]),
    ],
)
def test_parse_rejects_values_of_the_wrong_shape(setting, raw):
    with pytest.raises(ValueError):
        setting.parse(raw)


def test_secrets_are_stored_encrypted_with_only_a_hint(db_session, admin_user):
    save(db_session, VOYAGE_API_KEY, "pa-secret-1234", admin_user.id)
    db_session.flush()

    row = db_session.get(InstanceSetting, "voyage_api_key")
    assert row is not None
    assert (row.value, row.key_hint) == (None, "1234")
    assert row.value_encrypted is not None and b"pa-secret" not in row.value_encrypted
    assert stored_values_in(db_session) == {"voyage_api_key": "pa-secret-1234"}


def test_a_saved_value_is_live_once_the_cache_is_invalidated(db_session, admin_user, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_RERANKING", None)
    assert instance_settings.value(ENABLE_RERANKING) is True

    save(db_session, ENABLE_RERANKING, False, admin_user.id)
    db_session.flush()
    assert instance_settings.value(ENABLE_RERANKING) is True

    instance_settings.invalidate_cache()
    assert instance_settings.value(ENABLE_RERANKING) is False
