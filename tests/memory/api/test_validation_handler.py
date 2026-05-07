"""Tests for the global RequestValidationError handler.

Regression tests for the credential-leak bug where the handler logged the
raw request body and echoed `exc.body`/`input` back to the caller.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from memory.api.app import (
    SENSITIVE_QUERY_PARAMS,
    SENSITIVE_VALIDATION_FIELDS,
    redact_query_params,
    redact_validation_errors,
    validation_exception_handler,
)


# ====== unit tests for redaction helpers ======


@pytest.mark.parametrize(
    "field",
    [
        "password",
        "current_password",
        "new_password",
        "old_password",
        "token",
        "refresh_token",
        "access_token",
        "api_key",
        "client_secret",
        "secret",
        "value",
        "caldav_password",
        "webhook_secret",
    ],
)
def test_sensitive_field_constant_includes(field: str):
    assert field in SENSITIVE_VALIDATION_FIELDS


def test_redact_validation_errors_strips_input_unconditionally():
    errors = [
        {"loc": ("body", "name"), "msg": "x", "type": "t", "input": "public"},
        {"loc": ("body", "password"), "msg": "x", "type": "t", "input": "hunter2"},
    ]
    out = redact_validation_errors(errors)
    assert all("input" not in e for e in out)


def test_redact_validation_errors_drops_ctx_for_sensitive_fields():
    errors: list[dict[str, Any]] = [
        {
            "loc": ("body", "password"),
            "msg": "string too short",
            "type": "string_too_short",
            "input": "weak",
            "ctx": {"min_length": 8, "value": "weak"},
        }
    ]
    out = redact_validation_errors(errors)
    assert "input" not in out[0]
    # ctx removed entirely when loc names a sensitive field — even seemingly
    # benign keys like min_length aren't worth re-leaking the value via.
    assert "ctx" not in out[0]


def test_redact_validation_errors_keeps_ctx_for_non_sensitive_fields():
    errors: list[dict[str, Any]] = [
        {
            "loc": ("body", "username"),
            "msg": "x",
            "type": "t",
            "input": "alice",
            "ctx": {"min_length": 3},
        }
    ]
    out = redact_validation_errors(errors)
    assert out[0]["ctx"] == {"min_length": 3}


@pytest.mark.parametrize(
    "loc_path",
    [
        ("body", "password"),
        ("body", "PASSWORD"),  # case-insensitive
        ("body", "user", "current_password"),
        ("query", "api_key"),
    ],
)
def test_redact_validation_errors_recognizes_nested_sensitive_paths(loc_path):
    errors = [{"loc": loc_path, "msg": "x", "type": "t", "input": "secret", "ctx": {"min_length": 1}}]
    out = redact_validation_errors(errors)
    assert "input" not in out[0]
    assert "ctx" not in out[0]


@pytest.mark.parametrize("param", list(SENSITIVE_QUERY_PARAMS))
def test_redact_query_params_masks_sensitive(param: str):
    assert redact_query_params({param: "secret-value"})[param] == "***"


def test_redact_query_params_passes_through_non_sensitive():
    assert redact_query_params({"page": "2", "name": "alice"}) == {"page": "2", "name": "alice"}


def test_redact_query_params_is_case_insensitive():
    assert redact_query_params({"Token": "abc"}) == {"Token": "***"}
    assert redact_query_params({"API_KEY": "abc"}) == {"API_KEY": "***"}


# ====== integration: handler against a tiny isolated FastAPI app ======


class _LoginPayload(BaseModel):
    # username with min_length triggers a validation failure when too short,
    # which lets us assert that an unrelated `password` field present in the
    # SAME body is NOT logged or echoed back.
    username: str = Field(min_length=4)
    password: str
    api_key: str | None = None


def _make_isolated_app() -> FastAPI:
    """Build a throwaway FastAPI app that uses the production handler.

    Avoids the global app's lifespan / DB / MCP setup, which require a real
    Postgres and would skip under the slow-test gating.
    """
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]

    @app.post("/login")
    def login(payload: _LoginPayload) -> dict[str, str]:
        return {"status": "ok"}

    return app


def test_handler_does_not_log_raw_body(caplog: pytest.LogCaptureFixture):
    """The plaintext password sitting in the request body must not be logged."""
    app = _make_isolated_app()
    client = TestClient(app)
    caplog.set_level(logging.WARNING, logger="memory.api.app")

    secret = "PlaintextPasswordVerySensitive"
    response = client.post(
        "/login",
        json={"username": "ab", "password": secret},  # username too short → 422
    )

    assert response.status_code == 422
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert secret not in log_text


def test_handler_response_does_not_echo_body():
    """The 422 response must not contain `body` or the offending value."""
    app = _make_isolated_app()
    client = TestClient(app)

    secret = "AnotherPlaintextPasswordX9"
    response = client.post(
        "/login",
        json={"username": "ab", "password": secret},  # username too short → 422
    )

    assert response.status_code == 422
    body = response.json()
    # No body echo at all
    assert "body" not in body
    # Pydantic's `input` must not have leaked through any error
    assert all("input" not in err for err in body["detail"])
    # Plaintext secret must not appear anywhere in the response
    assert secret not in response.text


def test_handler_redacts_password_field_when_it_is_the_failing_field(
    caplog: pytest.LogCaptureFixture,
):
    """Even when password itself is what failed validation, its value mustn't leak."""

    class _ChangePassword(BaseModel):
        new_password: str = Field(min_length=8)

    app = FastAPI()
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]

    @app.post("/change-password")
    def change_password(payload: _ChangePassword) -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(app)
    caplog.set_level(logging.WARNING, logger="memory.api.app")

    secret = "weak2"  # 5 chars, < 8 → 422; this whole string is the secret
    response = client.post("/change-password", json={"new_password": secret})

    assert response.status_code == 422
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert secret not in log_text
    assert secret not in response.text


def test_handler_redacts_sensitive_query_params(caplog: pytest.LogCaptureFixture):
    """Sensitive query params (e.g. ?token=...) must be masked in logs."""
    app = _make_isolated_app()
    client = TestClient(app)
    caplog.set_level(logging.WARNING, logger="memory.api.app")

    token = "PlaintextTokenABCDEF123456"
    client.post(
        f"/login?token={token}",
        json={"username": "ab", "password": "x"},
    )

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert token not in log_text
    # Confirm the query param key is still logged (just masked) so SREs can
    # see *which* sensitive params were present.
    assert "token" in log_text.lower()


def test_handler_keeps_non_secret_loc_and_msg_for_debuggability(
    caplog: pytest.LogCaptureFixture,
):
    """The structured loc/msg/type must still be logged so 422s remain debuggable."""
    app = _make_isolated_app()
    client = TestClient(app)
    caplog.set_level(logging.WARNING, logger="memory.api.app")

    client.post(
        "/login",
        json={"username": "ab", "password": "anything"},
    )

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    # Field name and validation type are not secrets — they tell us what failed.
    assert "username" in log_text
    assert "string_too_short" in log_text
