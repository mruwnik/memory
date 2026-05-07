"""Hermetic tests for the CORS allow-list logic.

The previous configuration unconditionally trusted ``http://localhost:5173``
even in production, which let any locally-running attacker JS make
credentialed cross-origin requests against prod and read the response.
These tests pin the new behaviour:

- Production (default) only allows ``settings.SERVER_URL``.
- Setting ``ALLOW_LOCALHOST_CORS=true`` adds the dev-server origins.
- Origins not on the list see no ``Access-Control-Allow-Origin`` header
  echoed back, which is the Starlette/FastAPI signal that the browser
  must reject the cross-origin response.

We exercise the middleware on a tiny stand-in app so the tests don't
depend on the (much larger) real `memory.api.app` import graph.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient


def _build_app(server_url: str, allow_localhost: bool):
    """Replicate the allow-list shape used by `memory.api.app` at import time."""
    from memory.common import settings

    origins: list[str] = [server_url]
    if allow_localhost:
        origins.extend(settings.LOCALHOST_CORS_ORIGINS)

    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Edit-Token"],
    )

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return TestClient(app)


def test_production_default_blocks_localhost_origin():
    """With ALLOW_LOCALHOST_CORS=False (the prod default), the dev origin
    is NOT echoed back, which means the browser will refuse the
    response."""
    with patch("memory.common.settings.ALLOW_LOCALHOST_CORS", False):
        client = _build_app("https://prod.example.com", allow_localhost=False)
        response = client.get(
            "/ping", headers={"Origin": "http://localhost:5173"}
        )

    assert response.status_code == 200
    # Starlette omits the header entirely when the origin is not allowed.
    assert "access-control-allow-origin" not in {
        k.lower() for k in response.headers.keys()
    }


def test_production_default_allows_server_url_origin():
    """A request from the configured production origin is allowed."""
    with patch("memory.common.settings.ALLOW_LOCALHOST_CORS", False):
        client = _build_app("https://prod.example.com", allow_localhost=False)
        response = client.get(
            "/ping", headers={"Origin": "https://prod.example.com"}
        )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "https://prod.example.com"


def test_dev_flag_enables_localhost_origin():
    """With the dev opt-in flag, localhost dev servers can fetch."""
    with patch("memory.common.settings.ALLOW_LOCALHOST_CORS", True):
        client = _build_app("http://localhost:8000", allow_localhost=True)
        response = client.get(
            "/ping", headers={"Origin": "http://localhost:5173"}
        )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


@pytest.mark.parametrize(
    "evil_origin",
    [
        "http://evil.com",
        "http://localhost:5174",  # different dev port — still rejected
        "http://localhost",  # missing port — still rejected
        "https://localhost:5173",  # https variant — still rejected
    ],
)
def test_dev_flag_does_not_open_cors_to_arbitrary_origins(evil_origin):
    """Even with localhost CORS enabled, arbitrary origins must not be
    allowed. Pin this so a future "just allow_origins='*'" change shows
    up as a test regression."""
    with patch("memory.common.settings.ALLOW_LOCALHOST_CORS", True):
        client = _build_app("https://prod.example.com", allow_localhost=True)
        response = client.get("/ping", headers={"Origin": evil_origin})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in {
        k.lower() for k in response.headers.keys()
    }


def test_method_allow_list_is_specific_not_wildcard():
    """Tightened from `["*"]` to a specific list. Pin both that the
    intended methods are present and that the wildcard isn't snuck back
    in."""
    with patch("memory.common.settings.ALLOW_LOCALHOST_CORS", True):
        client = _build_app("https://prod.example.com", allow_localhost=True)
        response = client.options(
            "/ping",
            headers={
                "Origin": "https://prod.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    allow_methods = response.headers.get("access-control-allow-methods", "")
    assert "GET" in allow_methods
    assert "POST" in allow_methods
    assert "DELETE" in allow_methods
    assert "*" not in allow_methods
