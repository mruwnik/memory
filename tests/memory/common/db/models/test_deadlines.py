"""Tests for the Deadline model + deadline_attachments junction."""

from datetime import date, datetime

import pytest

from memory.common.content_processing import create_content_hash
from memory.common.db.models import Deadline, SourceItem
from memory.common.db.models.deadlines import deadline_attachments


@pytest.fixture
def source_item(db_session):
    item = SourceItem(
        modality="text",
        sha256=create_content_hash("attachment-1 content"),
        content="attachment-1 content",
        sensitivity="basic",
    )
    db_session.add(item)
    db_session.commit()
    return item


@pytest.fixture
def second_source_item(db_session):
    item = SourceItem(
        modality="text",
        sha256=create_content_hash("attachment-2 content"),
        content="attachment-2 content",
        sensitivity="basic",
    )
    db_session.add(item)
    db_session.commit()
    return item


def test_deadline_round_trip(db_session, admin_user):
    deadline = Deadline(
        title="Grant: NSF #1234",
        date=date(2026, 6, 30),
        description="Submit by 5pm Eastern",
        priority="urgent",
        sensitivity="basic",
        creator_id=admin_user.id,
        tags=["grant", "fy26"],
    )
    db_session.add(deadline)
    db_session.commit()
    db_session.refresh(deadline)

    assert deadline.id is not None
    assert deadline.title == "Grant: NSF #1234"
    assert deadline.date == date(2026, 6, 30)
    assert deadline.priority == "urgent"
    assert deadline.tags == ["grant", "fy26"]
    assert isinstance(deadline.created_at, datetime)
    assert deadline.attachments == []


def test_deadline_payload(db_session, admin_user, source_item):
    deadline = Deadline(
        title="Trip",
        date=date(2026, 8, 25),
        priority="medium",
        creator_id=admin_user.id,
        tags=["travel"],
    )
    deadline.attachments = [source_item]
    db_session.add(deadline)
    db_session.commit()
    db_session.refresh(deadline)

    payload = deadline.as_payload()
    assert payload["title"] == "Trip"
    assert payload["date"] == "2026-08-25"
    assert payload["priority"] == "medium"
    assert payload["sensitivity"] == "basic"
    assert payload["tags"] == ["travel"]
    assert payload["attachment_ids"] == [source_item.id]


@pytest.mark.parametrize(
    "priority", ["low", "medium", "high", "urgent", None]
)
def test_priority_values_accepted(db_session, admin_user, priority):
    deadline = Deadline(
        title=f"d-{priority}",
        date=date(2026, 7, 1),
        priority=priority,
        creator_id=admin_user.id,
    )
    db_session.add(deadline)
    db_session.commit()
    assert deadline.priority == priority


def test_priority_check_constraint_rejects_invalid(db_session, admin_user):
    from sqlalchemy.exc import IntegrityError

    deadline = Deadline(
        title="bad-priority",
        date=date(2026, 7, 1),
        priority="screaming",
        creator_id=admin_user.id,
    )
    db_session.add(deadline)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_attach_detach_via_relationship(
    db_session, admin_user, source_item, second_source_item
):
    deadline = Deadline(
        title="Multi-attach",
        date=date(2026, 9, 1),
        creator_id=admin_user.id,
    )
    deadline.attachments = [source_item, second_source_item]
    db_session.add(deadline)
    db_session.commit()
    db_session.refresh(deadline)

    assert {a.id for a in deadline.attachments} == {
        source_item.id,
        second_source_item.id,
    }

    deadline.attachments = [source_item]
    db_session.commit()
    db_session.refresh(deadline)
    assert {a.id for a in deadline.attachments} == {source_item.id}


def test_delete_deadline_keeps_source_item(
    db_session, admin_user, source_item
):
    deadline = Deadline(
        title="dropme",
        date=date(2026, 9, 1),
        creator_id=admin_user.id,
    )
    deadline.attachments = [source_item]
    db_session.add(deadline)
    db_session.commit()
    deadline_id = deadline.id

    db_session.delete(deadline)
    db_session.commit()

    assert db_session.get(Deadline, deadline_id) is None
    # Source item survives, junction row gone (cascade).
    assert db_session.get(SourceItem, source_item.id) is not None
    junction_rows = (
        db_session.query(deadline_attachments)
        .filter(deadline_attachments.c.deadline_id == deadline_id)
        .all()
    )
    assert junction_rows == []


def test_delete_source_item_keeps_deadline(
    db_session, admin_user, source_item
):
    deadline = Deadline(
        title="keepme",
        date=date(2026, 9, 1),
        creator_id=admin_user.id,
    )
    deadline.attachments = [source_item]
    db_session.add(deadline)
    db_session.commit()
    deadline_id = deadline.id
    item_id = source_item.id

    db_session.delete(source_item)
    db_session.commit()

    surviving = db_session.get(Deadline, deadline_id)
    assert surviving is not None
    assert surviving.attachments == []
    junction_rows = (
        db_session.query(deadline_attachments)
        .filter(deadline_attachments.c.source_item_id == item_id)
        .all()
    )
    assert junction_rows == []


def test_access_control_columns_present(db_session, admin_user):
    """Mixin-provided AC columns must be settable / queryable on Deadline."""
    deadline = Deadline(
        title="ac-test",
        date=date(2026, 9, 1),
        sensitivity="confidential",
        creator_id=admin_user.id,
        project_id=None,
    )
    db_session.add(deadline)
    db_session.commit()
    db_session.refresh(deadline)

    assert deadline.sensitivity == "confidential"
    assert deadline.creator_id == admin_user.id
    assert deadline.project_id is None
