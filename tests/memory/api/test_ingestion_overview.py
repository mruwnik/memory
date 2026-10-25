from datetime import datetime, timedelta, timezone

import pytest

from memory.common.db.models import Note
from memory.common.db.models.source_items import Photo


def test_inventory_counts_records_statuses_types_and_recent_items(
    client, user, db_session
):
    now = datetime.now(timezone.utc)
    for n, status in enumerate(["RAW", "QUEUED", "STORED", "FAILED", "SKIPPED"]):
        db_session.add(
            Note(
                sha256=f"n{n}".encode(),
                modality="text",
                creator_id=user.id,
                embed_status=status,
                inserted_at=now - timedelta(days=n),
            )
        )
    db_session.add(
        Photo(
            sha256=b"p",
            modality="photo",
            creator_id=user.id,
            embed_status="STORED",
            inserted_at=now,
        )
    )
    db_session.commit()
    response = client.get("/ingestion-overview")
    assert response.status_code == 200
    data = response.json()
    assert {
        k: data[k]
        for k in ["total", "indexed", "pending", "failed", "skipped", "added_last_24h"]
    } == {
        "total": 6,
        "indexed": 2,
        "pending": 2,
        "failed": 1,
        "skipped": 1,
        "added_last_24h": 2,
    }
    assert [(g["item_type"], g["total"]) for g in data["by_type"]] == [
        ("note", 5),
        ("photo", 1),
    ]
    assert datetime.fromisoformat(data["last_ingested_at"]) == now


@pytest.mark.parametrize("admin,expected", [(False, 1), (True, 2)])
def test_inventory_does_not_reveal_other_users_or_hidden_items(
    client, user, regular_user, db_session, admin, expected
):
    user.scopes = ["*"] if admin else ["read", "write"]
    db_session.add_all(
        [
            Note(sha256=b"own", modality="text", creator_id=user.id),
            Note(sha256=b"other", modality="text", creator_id=regular_user.id),
            Note(
                sha256=b"hidden",
                modality="text",
                creator_id=user.id,
                sensitivity="hidden",
            ),
        ]
    )
    db_session.commit()
    assert client.get("/ingestion-overview").json()["total"] == expected


def test_empty_inventory(client):
    data = client.get("/ingestion-overview").json()
    assert data["total"] == 0
    assert data["by_type"] == []
    assert data["last_ingested_at"] is None
