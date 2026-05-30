"""Tests for the /ingest/upload endpoint and land_and_dispatch helper."""

import pathlib
from unittest.mock import MagicMock, patch

import pytest

from memory.api import ingest_tokens as it
from memory.api.ingest import build_task_kwargs
from memory.common import ingest_routing
from memory.common import settings


def _intent(**kw):
    base = dict(
        user_id=42, type="application/pdf", filename="f", tags=["t"],
        doc_metadata={"title": "Tt", "author": "Aa", "k": "v"},
        project_id=9, exp=None,
    )
    base.update(kw)
    return it.IngestTokenPayload(**base)


@pytest.mark.parametrize("bucket", ["book", "image", "misc"])
def test_build_task_kwargs_carries_owner_for_every_bucket(bucket):
    spec = getattr(ingest_routing, f"{bucket}_spec")()
    kw = build_task_kwargs(spec, pathlib.Path("/tmp/x"), _intent())
    assert kw["creator_id"] == 42
    assert kw["project_id"] == 9
    if bucket == "book":
        assert kw["title"] == "Tt" and kw["author"] == "Aa"
    if bucket == "misc":
        assert kw["mime_type"] == "application/pdf"
        assert kw["doc_metadata"] == {"title": "Tt", "author": "Aa", "k": "v"}


SECRET = "test-secret-key-for-ingest-endpoint-tests"


@pytest.fixture(autouse=True)
def patch_secret():
    with patch("memory.common.settings.TRANSFER_TOKEN_SECRET", SECRET):
        yield


def _mint(user_id, mime_type="application/pdf", filename="doc.pdf"):
    return it.mint_token(
        it.IngestTokenPayload(
            user_id=user_id,
            type=mime_type,
            filename=filename,
            tags=["t"],
            doc_metadata={"src": "x"},
            project_id=None,
            exp=None,
        ),
        ttl_seconds=120,
    )


def _make_dispatch_result(job_id=7, celery_task_id="abc", status="queued", is_new=True):
    result = MagicMock()
    result.job.id = job_id
    result.job.celery_task_id = celery_task_id
    result.job.status = status
    result.is_new = is_new
    return result


def test_upload_lands_and_dispatches(client, user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MISC_STORAGE_DIR", tmp_path / "misc")
    token = _mint(user.id)
    with patch("memory.api.ingest.dispatch_job", return_value=_make_dispatch_result()) as dispatch:
        resp = client.post(f"/ingest/upload?token={token}", content=b"%PDF-1.4 fake")
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == 7
    assert body["task_id"] == "abc"
    assert body["status"] == "queued"
    dispatched_kwargs = dispatch.call_args.kwargs["task_kwargs"]
    assert dispatched_kwargs["mime_type"] == "application/pdf"
    assert dispatched_kwargs["doc_metadata"] == {"src": "x"}


def test_upload_rejects_bad_token(client):
    resp = client.post("/ingest/upload?token=v1.bad.sig", content=b"x")
    assert resp.status_code in (400, 401, 403)


def test_upload_rejects_oversize(client, user, monkeypatch):
    monkeypatch.setattr(settings, "MAX_MISC_UPLOAD_BYTES", 4)
    token = _mint(user.id)
    resp = client.post(f"/ingest/upload?token={token}", content=b"way too many bytes")
    assert resp.status_code == 413


def test_upload_expired_token(client, user):
    import time
    payload = it.IngestTokenPayload(
        user_id=user.id,
        type="application/pdf",
        filename="doc.pdf",
        tags=[],
        doc_metadata={},
        project_id=None,
        exp=int(time.time()) - 1,
    )
    token = it.mint_token(payload)
    resp = client.post(f"/ingest/upload?token={token}", content=b"%PDF-1.4 fake")
    assert resp.status_code == 401


def test_upload_missing_token(client):
    resp = client.post("/ingest/upload", content=b"data")
    # FastAPI returns 422 when required query param is missing
    assert resp.status_code == 422


def test_upload_image_routes_to_image_bucket(client, user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PHOTO_STORAGE_DIR", tmp_path / "photos")
    token = _mint(user.id, mime_type="image/jpeg", filename="photo.jpg")
    with patch("memory.api.ingest.dispatch_job", return_value=_make_dispatch_result()) as dispatch:
        resp = client.post(f"/ingest/upload?token={token}", content=b"\xff\xd8\xff fake jpeg")
    assert resp.status_code == 200
    dispatched_kwargs = dispatch.call_args.kwargs["task_kwargs"]
    assert "file_path" in dispatched_kwargs
    assert "tags" in dispatched_kwargs
    # image bucket does NOT pass mime_type
    assert "mime_type" not in dispatched_kwargs


def test_upload_duplicate_returns_existing_status(client, user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MISC_STORAGE_DIR", tmp_path / "misc")
    token = _mint(user.id)
    result = _make_dispatch_result(job_id=42, status="completed", is_new=False)
    with patch("memory.api.ingest.dispatch_job", return_value=result):
        resp = client.post(f"/ingest/upload?token={token}", content=b"%PDF-1.4 fake")
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == 42
    assert body["status"] == "completed"
