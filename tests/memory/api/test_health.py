import json
from unittest.mock import MagicMock

import httpx
import pytest

from memory.api.MCP import base
from memory.common import version


@pytest.mark.asyncio
async def test_health_stays_healthy_when_release_check_raises(monkeypatch, db_engine):
    async def failing_get(self, url, **kwargs):
        raise RuntimeError("release source exploded")

    monkeypatch.setattr(httpx.AsyncClient, "get", failing_get)
    monkeypatch.setattr(version.settings, "LATEST_RELEASE_URL", "https://example.test/latest")
    monkeypatch.setattr(version, "latest_release_cache", None)
    monkeypatch.setattr(base, "engine", db_engine)
    monkeypatch.setattr(base, "get_qdrant_client", MagicMock)

    response = await base.health_check(MagicMock())
    body = json.loads(bytes(response.body))

    assert response.status_code == 200
    assert body["database"] == "healthy"
    assert body["latest_release"] is None
    assert body["db_revision"] == body["expected_revision"]
