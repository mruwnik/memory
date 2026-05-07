"""Tests for the /api/docker/* admin-only access surface.

These tests are concerned exclusively with access control. The Docker side
is mocked away — what matters is that non-admin users get a 403 *before*
any Docker request is dispatched, and admins get past the gate so the
endpoint code runs.
"""

from unittest.mock import MagicMock, patch


def _make_docker_client_mock():
    """Return a context-manager mock that yields a Docker httpx.Client mock."""
    docker_client = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = docker_client
    cm.__exit__.return_value = False
    return cm, docker_client


def test_list_containers_forbidden_for_non_admin(regular_client):
    """Non-admin users must not be able to list containers."""
    # Patch get_docker_client so a 403 doesn't accidentally hit a real socket.
    with patch("memory.api.docker_logs.get_docker_client") as mock_factory:
        cm, _ = _make_docker_client_mock()
        mock_factory.return_value = cm

        response = regular_client.get("/api/docker/containers")

        assert response.status_code == 403
        # The Docker client must never have been opened for a non-admin call.
        mock_factory.assert_not_called()


def test_get_logs_forbidden_for_non_admin(regular_client):
    """Non-admin users must not be able to read container logs."""
    with patch("memory.api.docker_logs.get_docker_client") as mock_factory:
        cm, _ = _make_docker_client_mock()
        mock_factory.return_value = cm

        response = regular_client.get("/api/docker/logs/memory-api?tail=100")

        assert response.status_code == 403
        mock_factory.assert_not_called()


def test_list_containers_allowed_for_admin(client):
    """Admins (the default test client uses scopes=['*']) get past the gate."""
    with patch("memory.api.docker_logs.get_docker_client") as mock_factory:
        cm, docker_client = _make_docker_client_mock()
        mock_factory.return_value = cm

        api_response = MagicMock()
        api_response.json.return_value = []
        api_response.raise_for_status = MagicMock()
        docker_client.get.return_value = api_response

        response = client.get("/api/docker/containers")

        assert response.status_code == 200
        # Confirm the admin path actually invoked the Docker layer.
        docker_client.get.assert_called_once()


def test_get_logs_allowed_for_admin(client):
    """Admins get past the gate and the Docker fetch is dispatched."""
    with patch("memory.api.docker_logs.get_docker_client") as mock_factory:
        cm, docker_client = _make_docker_client_mock()
        mock_factory.return_value = cm

        api_response = MagicMock()
        api_response.content = b""
        api_response.raise_for_status = MagicMock()
        docker_client.get.return_value = api_response

        response = client.get("/api/docker/logs/memory-api?tail=10")

        assert response.status_code == 200
        body = response.json()
        assert body["container"] == "memory-api"
        assert body["lines"] == 0
        docker_client.get.assert_called_once()
