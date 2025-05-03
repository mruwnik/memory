from memory.common.db.models import SourceItem
from sqlalchemy.orm import Session


def test_unique_source_items_same_commit(db_session: Session):
    source_item1 = SourceItem(sha256=b"1234567890", content="test1", modality="email")
    source_item2 = SourceItem(sha256=b"1234567890", content="test2", modality="email")
    source_item3 = SourceItem(sha256=b"1234567891", content="test3", modality="email")
    db_session.add(source_item1)
    db_session.add(source_item2)
    db_session.add(source_item3)
    db_session.commit()

    assert db_session.query(SourceItem.sha256, SourceItem.content).all() == [
        (b"1234567890", "test1"),
        (b"1234567891", "test3"),
    ]


def test_unique_source_items_previous_commit(db_session: Session):
    db_session.add_all(
        [
            SourceItem(sha256=b"1234567890", content="test1", modality="email"),
            SourceItem(sha256=b"1234567891", content="test2", modality="email"),
            SourceItem(sha256=b"1234567892", content="test3", modality="email"),
        ]
    )
    db_session.commit()

    db_session.add_all(
        [
            SourceItem(sha256=b"1234567890", content="test4", modality="email"),
            SourceItem(sha256=b"1234567893", content="test5", modality="email"),
            SourceItem(sha256=b"1234567894", content="test6", modality="email"),
        ]
    )
    db_session.commit()

    assert db_session.query(SourceItem.sha256, SourceItem.content).all() == [
        (b"1234567890", "test1"),
        (b"1234567891", "test2"),
        (b"1234567892", "test3"),
        (b"1234567893", "test5"),
        (b"1234567894", "test6"),
    ]
