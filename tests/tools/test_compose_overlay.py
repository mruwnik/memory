import pathlib

import yaml

REPO_ROOT = pathlib.Path(__file__).parents[2]


def load(name: str) -> dict:
    return yaml.safe_load((REPO_ROOT / name).read_text())


def test_base_compose_has_no_claude_container_wiring():
    compose = load("docker-compose.yaml")
    api = compose["services"]["api"]

    assert "memory-api" not in compose["networks"]
    assert "memory-api" not in api["networks"]
    assert "group_add" not in api
    assert "ORCHESTRATOR_SOCKET" not in api["environment"]
    assert not any("claude-sessions" in v for v in api["volumes"])


def test_overlay_wires_api_to_the_orchestrator():
    overlay = load("docker-compose.claude.yaml")
    api = overlay["services"]["api"]

    assert overlay["networks"]["memory-api"]["external"] is True
    assert "memory-api" in api["networks"]
    assert api["group_add"]
    assert "ORCHESTRATOR_SOCKET" in api["environment"]
    assert any("claude-sessions" in v for v in api["volumes"])
