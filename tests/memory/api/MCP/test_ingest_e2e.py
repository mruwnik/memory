"""End-to-end: add_content (inline) -> land_and_dispatch -> dispatch_job ->
the dispatched kwargs run through the real sync_misc_doc task -> a MiscDoc row.

Only the Celery broker hop (send_task) is stubbed; every other layer
(routing, file landing, job dispatch, task body, model chunking) runs for real.
"""

import base64
import hashlib
from unittest.mock import MagicMock, patch

import pytest

from memory.common.db.models import MiscDoc
from memory.api.MCP.servers.ingest import add_content
from memory.workers.tasks.misc import sync_misc_doc
from tests.conftest import mcp_auth_context


@pytest.mark.asyncio
async def test_add_content_inline_creates_miscdoc(db_session, admin_session, admin_user):
    body = b"end to end generic ingestion content, plenty to chunk.\n" * 4
    data = base64.b64encode(body).decode()

    captured: dict = {}

    def fake_send_task(task_name, kwargs=None, **_):
        captured["task_name"] = task_name
        captured["kwargs"] = kwargs
        result = MagicMock()
        result.id = "fake-task-id"
        return result

    with mcp_auth_context(admin_session.id):
        with patch(
            "memory.common.jobs.celery_app.send_task", side_effect=fake_send_task
        ):
            res = await add_content.fn(
                type="text/plain",
                name="e2e.txt",
                data=data,
                tags=["e2e"],
                metadata={"origin": "test"},
            )

    # The tool dispatched the misc task with the right routing + kwargs.
    assert res["status"] == "queued"
    assert captured["task_name"].endswith("sync_misc_doc")
    assert captured["kwargs"]["mime_type"] == "text/plain"
    assert captured["kwargs"]["doc_metadata"] == {"origin": "test"}
    assert captured["kwargs"]["tags"] == ["e2e"]

    # Simulate the worker running the dispatched task on those exact kwargs.
    sync_misc_doc(**captured["kwargs"])

    doc = (
        db_session.query(MiscDoc)
        .filter(MiscDoc.sha256 == hashlib.sha256(body).digest())
        .one()
    )
    assert doc.mime_type == "text/plain"
    assert doc.tags == ["e2e"]
    assert doc.doc_metadata == {"origin": "test"}
    # The owner is carried end-to-end (content is visible to its creator, not
    # admin-only) — this is the headline behavior of the iteration.
    assert doc.creator_id == admin_user.id
    # The landed file is readable and chunked (non-empty) through the real model.
    assert doc._chunk_contents()
