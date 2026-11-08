import re
from pathlib import Path

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from starlette.routing import BaseRoute, Mount

from memory.api.app import app
from memory.api.web_ui import is_backend_path, static_file
from memory.common import settings

INDEX = "<!doctype html><title>memory-spa</title>"
HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
APP_TSX = Path(__file__).parents[3] / "frontend" / "src" / "App.tsx"


@pytest.fixture
def web(tmp_path, monkeypatch):
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text(INDEX)
    (static / "assets" / "app.js").write_text("console.log('app')")
    (static / "mcp-client.js").write_text("// client")
    (tmp_path / "secret.txt").write_text("secret")
    monkeypatch.setattr(settings, "STATIC_DIR", static)
    monkeypatch.setattr(settings, "DISABLE_AUTH", False)
    return TestClient(app, raise_server_exceptions=False)


def route_paths(routes: list[BaseRoute], prefix: str = "") -> list[str]:
    return [
        path
        for context in iter_route_contexts(routes)
        for path in (
            route_paths(context.original_route.routes, prefix + context.path)
            if isinstance(context.original_route, Mount)
            else [prefix + context.path]
        )
    ]


def concrete(template: str) -> str:
    return re.sub(r"\{[^}]+\}|:\w+", "x", template)


@pytest.mark.parametrize(
    "path, location",
    [
        ("/ui", "/"),
        ("/ui/", "/"),
        ("/ui/sources?x=1", "/sources?x=1"),
        ("/ui/polls/respond/abc?a=1&b=2", "/polls/respond/abc?a=1&b=2"),
        ("/ui/mcp-client.js", "/mcp-client.js"),
        ("/ui//evil.com", "/evil.com"),
        ("/ui/%2F%2Fevil.com", "/evil.com"),
        ("/ui/%09/evil.com", "/evil.com"),
        ("/ui/%5C%5Cevil.com", "/evil.com"),
        ("/ui/%20//evil.com?x=1", "/evil.com?x=1"),
    ],
)
def test_ui_redirects_to_root_keeping_path_and_query(web, path, location):
    response = web.get(path, follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == location


@pytest.mark.parametrize("path", ["/", "/sources", "/agents/runs/5", "/polls/respond/abc", "/login"])
def test_navigation_to_client_route_serves_index_without_login(web, path):
    response = web.get(path, headers={"Accept": HTML})

    assert response.status_code == 200
    assert response.text == INDEX


@pytest.mark.parametrize("path", ["../secret.txt", "/../secret.txt", "assets/../../secret.txt", "", "assets"])
def test_static_file_stays_inside_the_build(web, path):
    assert static_file(path) is None


@pytest.mark.parametrize(
    "path, body",
    [("/assets/app.js", "console.log('app')"), ("/mcp-client.js", "// client"), ("/index.html", INDEX)],
)
def test_static_files_served_from_root(web, path, body):
    response = web.get(path, headers={"Accept": "*/*"})

    assert response.status_code == 200
    assert response.text == body


@pytest.mark.parametrize(
    "method, path, accept, status",
    [
        ("GET", "/sources", "*/*", 401),
        ("GET", "/sources", "application/json", 401),
        ("GET", "/api/nope", "application/json", 401),
        ("POST", "/sources", HTML, 401),
        ("PUT", "/", HTML, 404),
        ("GET", "/uix", "*/*", 401),
        ("GET", "/%2e%2e/secret.txt", "*/*", 401),
    ],
)
def test_non_navigations_to_unknown_paths_are_not_the_spa(web, method, path, accept, status):
    response = web.request(method, path, headers={"Accept": accept})

    assert response.status_code == status
    assert response.text not in (INDEX, "secret")


@pytest.mark.parametrize(
    "path",
    ["/health", "/.well-known/oauth-authorization-server", "/token", "/api/users", "/api/jobs", "/docs"],
)
def test_navigation_to_backend_route_reaches_backend(web, path):
    response = web.get(path, headers={"Accept": HTML})

    assert response.text != INDEX


@pytest.mark.parametrize("template", route_paths(app.routes))
def test_every_backend_route_is_recognised(template):
    scope = {"type": "http", "method": "GET", "path": concrete(template), "root_path": ""}

    assert is_backend_path(app.router, scope)


def client_routes() -> list[str]:
    source = APP_TSX.read_text()
    return re.findall(r"""path(?::\s*|=)['"](/[^'"]*)['"]""", source)


def test_client_routes_are_found():
    assert "/sources" in client_routes()


@pytest.mark.parametrize("template", client_routes())
def test_client_routes_do_not_clash_with_backend_routes(template):
    scope = {"type": "http", "method": "GET", "path": concrete(template), "root_path": ""}

    assert not is_backend_path(app.router, scope)
