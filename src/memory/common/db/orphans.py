"""Source items whose joined subclass row is gone.

Such a row can't be loaded: reading any subclass attribute raises. The
``delete_orphaned_source_item`` trigger removes the source_item row when its
subclass row is deleted; this finds rows left from before it, or by anything
that bypasses triggers.
"""

from collections.abc import Collection

from sqlalchemy import TableClause, and_, exists, or_, select
from sqlalchemy.orm import Session

from memory.common.db.models import SourceItem


def subclass_tables_by_identity() -> dict[str, list[TableClause]]:
    base = SourceItem.__table__
    return {
        mapper.polymorphic_identity: [t for t in mapper.tables if t is not base]
        for mapper in SourceItem.__mapper__.self_and_descendants
        if mapper.polymorphic_identity and any(t is not base for t in mapper.tables)
    }


def subclass_tables() -> list[str]:
    """Every joined-inheritance child table of source_item."""
    return sorted({t.name for tables in subclass_tables_by_identity().values() for t in tables})


def orphaned_source_ids(db: Session, ids: Collection[int] | None = None) -> list[int]:
    """Ids of source items (of ``ids``, if given) missing a subclass row."""
    source = SourceItem.__table__
    orphaned = or_(
        *(
            and_(
                source.c.type == identity,
                or_(*(~exists().where(t.c.id == source.c.id) for t in tables)),
            )
            for identity, tables in subclass_tables_by_identity().items()
        )
    )
    query = select(source.c.id).where(orphaned).order_by(source.c.id)
    if ids is not None:
        query = query.where(source.c.id.in_(ids))
    return list(db.scalars(query))
