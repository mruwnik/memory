import pathlib
import runpy
import sys

import pytest

from memory.common import settings
from memory.common.db.models.users import BotUser, HumanUser
from memory.common.modules import MULTI_USER
from memory.common.scopes import SCOPE_ADMIN

ADD_USER = pathlib.Path(__file__).parents[2] / "tools" / "add_user.py"


def run_add_user(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["add_user.py", *args])
    runpy.run_path(str(ADD_USER), run_name="__main__")


def test_password_comes_from_environment(db_session, monkeypatch):
    monkeypatch.setenv("ADD_USER_PASSWORD", "env-pw-123")

    run_add_user(monkeypatch, "--email", "env@example.com", "--name", "Env", "--no-api-key")

    user = db_session.query(HumanUser).filter_by(email="env@example.com").one()
    assert user.is_valid_password("env-pw-123")


def test_if_missing_skips_existing_user_instead_of_failing(db_session, monkeypatch):
    monkeypatch.setenv("ADD_USER_PASSWORD", "first-pw")
    args = ["--email", "dup@example.com", "--name", "Dup", "--no-api-key"]
    run_add_user(monkeypatch, *args)
    monkeypatch.setenv("ADD_USER_PASSWORD", "second-pw")

    with pytest.raises(SystemExit) as exit_info:
        run_add_user(monkeypatch, *args, "--if-missing")

    assert exit_info.value.code == 0
    user = db_session.query(HumanUser).filter_by(email="dup@example.com").one()
    assert user.is_valid_password("first-pw")


@pytest.mark.parametrize("flags, scopes", [([], ["read"]), (["--admin"], [SCOPE_ADMIN])])
def test_admin_flag_grants_full_access(db_session, monkeypatch, flags, scopes):
    monkeypatch.setenv("ADD_USER_PASSWORD", "pw")

    run_add_user(monkeypatch, "--email", "a@example.com", "--name", "A", "--no-api-key", *flags)

    assert db_session.query(HumanUser).filter_by(email="a@example.com").one().scopes == scopes


def test_single_user_install_makes_the_human_a_full_admin(db_session, monkeypatch):
    monkeypatch.setattr(settings, "ENABLED_MODULES", frozenset())
    monkeypatch.setenv("ADD_USER_PASSWORD", "pw")

    run_add_user(monkeypatch, "--email", "solo@example.com", "--name", "Solo", "--no-api-key")
    run_add_user(monkeypatch, "--email", "bot@example.com", "--name", "Bot", "--bot")

    assert db_session.query(HumanUser).filter_by(email="solo@example.com").one().scopes == [SCOPE_ADMIN]
    assert db_session.query(BotUser).filter_by(email="bot@example.com").one().scopes == ["read"]


def test_second_human_is_refused_without_multi_user(db_session, monkeypatch):
    monkeypatch.setattr(settings, "ENABLED_MODULES", frozenset())
    monkeypatch.setenv("ADD_USER_PASSWORD", "pw")
    run_add_user(monkeypatch, "--email", "one@example.com", "--name", "One", "--no-api-key")

    with pytest.raises(SystemExit, match="multi_user"):
        run_add_user(monkeypatch, "--email", "two@example.com", "--name", "Two", "--no-api-key")

    assert db_session.query(HumanUser).count() == 1


def test_multi_user_install_allows_a_second_human_with_read_scope(db_session, monkeypatch):
    monkeypatch.setattr(settings, "ENABLED_MODULES", frozenset({MULTI_USER}))
    monkeypatch.setenv("ADD_USER_PASSWORD", "pw")

    run_add_user(monkeypatch, "--email", "one@example.com", "--name", "One", "--no-api-key")
    run_add_user(monkeypatch, "--email", "two@example.com", "--name", "Two", "--no-api-key")

    humans = db_session.query(HumanUser).all()
    assert [h.scopes for h in humans] == [["read"], ["read"]]
