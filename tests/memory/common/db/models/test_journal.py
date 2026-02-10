"""Tests for JournalEntry model."""

from memory.common.content_processing import create_content_hash
from memory.common.db.models import JournalEntry, SourceItem, user_can_access_journal_entry


def test_journal_entry_creation(db_session, admin_user):
    item = SourceItem(
        modality="text",
        sha256=create_content_hash("test content"),
        content="test content",
    )
    db_session.add(item)
    db_session.flush()

    entry = JournalEntry(
        target_type="source_item",
        target_id=item.id,
        creator_id=admin_user.id,
        content="First journal entry",
    )
    db_session.add(entry)
    db_session.commit()

    assert entry.id is not None
    assert entry.target_id == item.id
    assert entry.private is False
    assert entry.created_at is not None


def test_journal_entry_private(db_session, admin_user):
    item = SourceItem(
        modality="text",
        sha256=create_content_hash("test2"),
        content="test2",
    )
    db_session.add(item)
    db_session.flush()

    entry = JournalEntry(
        target_type="source_item",
        target_id=item.id,
        creator_id=admin_user.id,
        content="Private",
        private=True,
    )
    db_session.add(entry)
    db_session.commit()

    assert entry.private is True


def test_journal_cascade_delete(db_session, admin_user):
    item = SourceItem(
        modality="text",
        sha256=create_content_hash("deleteme"),
        content="deleteme",
    )
    db_session.add(item)
    db_session.flush()

    entry = JournalEntry(target_type="source_item", target_id=item.id, creator_id=admin_user.id, content="gone")
    db_session.add(entry)
    db_session.commit()
    entry_id = entry.id

    db_session.delete(item)
    db_session.commit()

    assert db_session.get(JournalEntry, entry_id) is None


def test_access_private_creator_can_see(db_session, admin_user, regular_user):
    item = SourceItem(modality="text", sha256=create_content_hash("x"), content="x")
    db_session.add(item)
    db_session.flush()

    entry = JournalEntry(
        target_type="source_item",
        target_id=item.id,
        creator_id=regular_user.id,
        content="private",
        private=True,
    )
    db_session.add(entry)
    db_session.commit()

    assert user_can_access_journal_entry(regular_user, entry) is True
    assert user_can_access_journal_entry(admin_user, entry) is True


def test_access_private_others_cannot_see(db_session, admin_user, regular_user):
    item = SourceItem(modality="text", sha256=create_content_hash("y"), content="y")
    db_session.add(item)
    db_session.flush()

    entry = JournalEntry(
        target_type="source_item",
        target_id=item.id,
        creator_id=admin_user.id,
        content="admin private",
        private=True,
    )
    db_session.add(entry)
    db_session.commit()

    assert user_can_access_journal_entry(regular_user, entry) is False


def test_journal_relationship(db_session, admin_user):
    item = SourceItem(modality="text", sha256=create_content_hash("z"), content="z")
    db_session.add(item)
    db_session.flush()

    entry = JournalEntry(target_type="source_item", target_id=item.id, creator_id=admin_user.id, content="entry")
    db_session.add(entry)
    db_session.commit()

    db_session.refresh(item)
    assert len(item.journal_entries) == 1
    assert item.journal_entries[0].content == "entry"
