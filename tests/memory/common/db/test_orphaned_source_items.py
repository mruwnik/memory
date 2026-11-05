"""A source_item row goes when its subclass row does, however that row is deleted."""
import importlib.util
import warnings
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from memory.common.db.models import BlogPost, Book, BookSection, Chunk, SourceItem
from memory.common.db.orphans import orphaned_source_ids, subclass_tables
from tests.conftest import unique_sha256

TRIGGER_TABLES = text(
    "SELECT tgrelid::regclass::text FROM pg_trigger "
    "WHERE tgfoid = 'delete_orphaned_source_item'::regproc"
)


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261128_orphaned_source_items.py"
    spec = importlib.util.spec_from_file_location("orphaned_source_items_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def blog_post(db_session, name: str) -> BlogPost:
    post = BlogPost(
        sha256=unique_sha256(name), modality="blog", title=name, content="body", tags=[]
    )
    db_session.add(post)
    db_session.flush()
    db_session.add(Chunk(source_id=post.id, content=name))
    db_session.flush()
    return post


def fire_deferred_triggers(db_session):
    db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


def source_item_ids(db_session) -> set[int]:
    return set(db_session.scalars(text("SELECT id FROM source_item")))


def test_every_joined_subclass_table_has_the_orphan_trigger(db_session):
    """A new SourceItem subclass table needs the trigger: create it in its migration
    with the statement ``20261128_orphaned_source_items`` uses."""
    assert set(db_session.scalars(TRIGGER_TABLES)) == set(subclass_tables())


def test_deleting_a_subclass_row_deletes_its_source_item(db_session):
    kept = blog_post(db_session, "kept").id
    gone = blog_post(db_session, "gone").id

    db_session.execute(text("DELETE FROM blog_post WHERE id = :id"), {"id": gone})
    fire_deferred_triggers(db_session)

    assert source_item_ids(db_session) == {kept}
    assert set(db_session.scalars(text("SELECT source_id FROM chunk"))) == {kept}


def test_a_cascade_from_the_parent_table_deletes_the_source_items(db_session):
    book = Book(title="Book", author="Author", file_path="book.epub")
    db_session.add(book)
    db_session.flush()
    db_session.add(
        BookSection(book_id=book.id, section_title="One", modality="book", sha256=unique_sha256("one"))
    )
    db_session.flush()

    db_session.execute(text("DELETE FROM book WHERE id = :id"), {"id": book.id})
    fire_deferred_triggers(db_session)

    assert source_item_ids(db_session) == set()


def test_deleting_the_source_item_itself_still_works(db_session):
    by_sql = blog_post(db_session, "by sql").id
    by_orm = blog_post(db_session, "by orm")

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        db_session.execute(text("DELETE FROM source_item WHERE id = :id"), {"id": by_sql})
        db_session.delete(by_orm)
        db_session.flush()
        fire_deferred_triggers(db_session)

    assert source_item_ids(db_session) == set()


def test_orphaned_source_ids_finds_items_without_their_subclass_row(db_session):
    kept = blog_post(db_session, "kept").id
    gone = blog_post(db_session, "gone").id
    plain = SourceItem(sha256=unique_sha256("plain"), modality="blog", tags=[])
    db_session.add(plain)
    db_session.flush()
    db_session.execute(text("DELETE FROM blog_post WHERE id = :id"), {"id": gone})

    assert orphaned_source_ids(db_session) == [gone]
    assert orphaned_source_ids(db_session, [kept, plain.id]) == []


def test_downgrade_drops_the_triggers(db_session):
    module = load_migration()
    db = db_session.connection()

    with Operations.context(MigrationContext.configure(db)):
        module.downgrade()
    assert db.execute(
        text("SELECT count(*) FROM pg_proc WHERE proname = 'delete_orphaned_source_item'")
    ).scalar() == 0

    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()
    assert set(db.scalars(TRIGGER_TABLES)) == set(subclass_tables())
