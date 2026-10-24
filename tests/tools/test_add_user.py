import pathlib
import runpy
import sys

import pytest

from memory.common.db.models.users import HumanUser

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
