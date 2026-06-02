from memory.common.scopes import (
    SCOPE_CHECK,
    ALL_SCOPE_VALUES,
    validate_scopes,
)


def test_check_scope_is_registered():
    assert SCOPE_CHECK == "check"
    assert SCOPE_CHECK in ALL_SCOPE_VALUES


def test_check_scope_validates():
    assert validate_scopes(["check"]) == []
